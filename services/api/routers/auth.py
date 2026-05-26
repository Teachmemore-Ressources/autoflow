"""
Authentication
==============
Supports two mechanisms simultaneously — backward-compatible:

  1. X-API-Key header  (original, still works)
  2. Authorization: Bearer <jwt>  (new)

JWT tokens are issued via POST /api/v1/auth/token (OAuth2 password flow).

JWT Revocation
--------------
Tokens carry a ``jti`` (JWT ID) claim.  When REDIS_URL is configured,
POST /api/v1/auth/logout stores the jti in Redis with TTL = remaining
lifetime, making the token permanently invalid.  Without Redis the logout
endpoint still returns 200 but revocation is client-side only.
"""
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import jwt
import user_store
from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.security.api_key import APIKeyHeader
from limiter import limiter
from settings import settings

if TYPE_CHECKING:
    import redis.asyncio as aioredis

_log = logging.getLogger("autoflow.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Security scheme objects ───────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_oauth2_scheme  = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


# ── Redis revocation blacklist ────────────────────────────────────────────────

_redis_client: "aioredis.Redis | None" = None
_REDIS_PREFIX = "autoflow:revoked_jti:"


def _redis() -> "aioredis.Redis | None":
    """Return a lazily-initialised async Redis client, or None if REDIS_URL is unset."""
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is None:
        import redis.asyncio as aioredis  # noqa: PLC0415
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def _is_revoked(jti: str) -> bool:
    """Return True if *jti* is in the revocation blacklist."""
    client = _redis()
    if client is None:
        return False
    return bool(await client.exists(f"{_REDIS_PREFIX}{jti}"))


async def _revoke(jti: str, ttl_seconds: int) -> None:
    """Add *jti* to the revocation blacklist with the given TTL (seconds)."""
    client = _redis()
    if client is None:
        _log.warning(
            "JWT revocation requested but REDIS_URL is not set — "
            "token jti=%s will expire naturally", jti,
        )
        return
    await client.setex(f"{_REDIS_PREFIX}{jti}", ttl_seconds, "1")
    _log.info("Token revoked: jti=%s ttl=%ds", jti, ttl_seconds)


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_access_token(username: str, expires_minutes: int | None = None) -> str:
    """Mint a signed JWT with sub, exp, iat, and jti claims."""
    minutes = expires_minutes or settings.jwt_expire_minutes
    now     = datetime.now(timezone.utc)
    expire  = now + timedelta(minutes=minutes)
    return jwt.encode(
        {
            "sub": username,
            "exp": expire,
            "iat": now,
            "jti": str(uuid.uuid4()),   # unique token ID for revocation
        },
        settings.effective_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


async def _decode_token(token: str) -> str:
    """
    Decode a JWT, verify it is not revoked, and return the username (sub claim).
    Raises HTTP 401 on any error.
    """
    try:
        payload = jwt.decode(
            token,
            settings.effective_jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — use POST /api/v1/auth/token to obtain a new one",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    if jti and await _is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked — use POST /api/v1/auth/token to obtain a new one",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload["sub"]


# ── Auth dependency ───────────────────────────────────────────────────────────

async def require_auth(
    api_key: str | None = Security(_api_key_header),
    token:   str | None = Security(_oauth2_scheme),
) -> str:
    """
    Accepts either authentication mechanism:
      • X-API-Key: <value of API_SECRET_KEY>               (legacy, always works)
      • Authorization: Bearer <jwt from /api/v1/auth/token> (new, supports revocation)

    Returns the authenticated username on success.
    """
    # ── 1. X-API-Key (legacy) ─────────────────────────────────────────────────
    if api_key is not None:
        if hmac.compare_digest(api_key, settings.api_secret_key):
            return "admin"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    # ── 2. JWT Bearer ─────────────────────────────────────────────────────────
    if token is not None:
        return await _decode_token(token)

    # ── 3. Nothing provided ───────────────────────────────────────────────────
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide X-API-Key header or Bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Backward-compat alias (awx.py imported require_api_key before Phase 2)
require_api_key = require_auth


# ── RBAC helpers ──────────────────────────────────────────────────────────────

async def require_write_access(username: str = Depends(require_auth)) -> str:
    """
    Require at least **operator** role.

    - ``admin``    — allowed
    - ``operator`` — allowed
    - ``viewer``   — raises HTTP 403

    X-API-Key callers are treated as admin (backward compatibility).
    """
    role = user_store.get_role(username) or "admin"  # None → API-key caller → admin
    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="viewer role is read-only — use an operator or admin account to perform write operations",
        )
    return username


# ── Auth endpoints ────────────────────────────────────────────────────────────

@router.post(
    "/token",
    summary="Login — obtain a JWT access token",
    description="""
Standard OAuth2 password flow. Use the returned token in subsequent requests:

```
Authorization: Bearer <access_token>
```

**Default credentials (no extra configuration needed):**
- `username` : value of `API_USERNAME` (default: `admin`)
- `password` : value of `API_PASSWORD` (default: same as `API_SECRET_KEY`)

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \\
  -d "username=admin&password=<API_SECRET_KEY>"
```
""",
)
@limiter.limit("5/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    role = user_store.authenticate(form_data.username, form_data.password)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(form_data.username)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   settings.jwt_expire_minutes * 60,
        "username":     form_data.username,
        "role":         role,
    }


@router.post("/refresh", summary="Refresh — extend token expiry")
async def refresh_token(username: str = Depends(require_auth)):
    """Issue a new token with a fresh expiry. Accepts both API key and existing JWT."""
    token = create_access_token(username)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   settings.jwt_expire_minutes * 60,
        "username":     username,
    }


@router.get("/me", summary="Current user info")
async def me(username: str = Depends(require_auth)):
    """Returns the identity and role of the currently authenticated caller."""
    return {
        "username":           username,
        "role":               user_store.get_role(username) or "admin",  # API-key callers
        "jwt_algorithm":      settings.jwt_algorithm,
        "jwt_expire_minutes": settings.jwt_expire_minutes,
    }


@router.post(
    "/logout",
    summary="Logout — revoke the current JWT",
    description="""
Revoke the current Bearer token by blacklisting its `jti` in Redis.

After a successful logout:
- The token is **immediately rejected** by every subsequent request (when REDIS_URL is set).
- The blacklist entry expires automatically when the token would have expired anyway.

**Without REDIS_URL**: the endpoint returns 200 but revocation is client-side only
(the server has no memory of the invalidated token).

**X-API-Key callers**: logout is a no-op — API keys cannot be revoked here.
""",
)
async def logout(
    token:    str | None = Security(_oauth2_scheme),
    username: str        = Depends(require_auth),
):
    """Blacklist the current token's jti so it cannot be reused."""
    if token:
        try:
            payload = jwt.decode(
                token,
                settings.effective_jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                remaining = int(exp - datetime.now(timezone.utc).timestamp())
                if remaining > 0:
                    await _revoke(jti, remaining)
        except jwt.InvalidTokenError:
            pass  # Already expired or invalid — nothing to blacklist

    return {"status": "logged_out", "username": username}
