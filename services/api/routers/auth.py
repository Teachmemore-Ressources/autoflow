"""
Authentication
==============
Supports two mechanisms simultaneously — backward-compatible:

  1. X-API-Key header  (original, still works)
  2. Authorization: Bearer <jwt>  (new)

JWT tokens are issued via POST /auth/token (OAuth2 password flow).
"""
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.security.api_key import APIKeyHeader

from limiter import limiter
from settings import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Security scheme objects ───────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_oauth2_scheme  = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_access_token(username: str, expires_minutes: int | None = None) -> str:
    minutes = expires_minutes or settings.jwt_expire_minutes
    expire  = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode(
        {"sub": username, "exp": expire, "iat": datetime.now(timezone.utc)},
        settings.effective_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _decode_token(token: str) -> str:
    """Decode a JWT and return the username (sub claim). Raises 401 on any error."""
    try:
        payload = jwt.decode(
            token,
            settings.effective_jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — use POST /auth/token to obtain a new one",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Auth dependency ───────────────────────────────────────────────────────────

def require_auth(
    api_key: str | None = Security(_api_key_header),
    token:   str | None = Security(_oauth2_scheme),
) -> str:
    """
    Accepts either authentication mechanism:
      • X-API-Key: <value of API_SECRET_KEY>           (legacy, always works)
      • Authorization: Bearer <jwt from /auth/token>   (new)

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
        return _decode_token(token)

    # ── 3. Nothing provided ───────────────────────────────────────────────────
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide X-API-Key header or Bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Backward-compat alias (awx.py imported require_api_key before Phase 2)
require_api_key = require_auth


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
curl -X POST http://localhost:8000/auth/token \\
  -d "username=admin&password=<API_SECRET_KEY>"
```
""",
)
@limiter.limit("5/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    if (
        form_data.username != settings.api_username
        or form_data.password != settings.effective_api_password
    ):
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
    """Returns the identity of the currently authenticated caller."""
    return {
        "username":           username,
        "jwt_algorithm":      settings.jwt_algorithm,
        "jwt_expire_minutes": settings.jwt_expire_minutes,
    }
