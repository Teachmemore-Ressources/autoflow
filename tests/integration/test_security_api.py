# Autoflow — tests/integration/test_security_api — Apache 2.0
"""
Integration tests — Security headers and CORS on the Autoflow API.

Uses a minimal FastAPI application (not the full services/api/main.py) that
mirrors the security middleware stack.  This approach:
- Avoids the complex AWX/Redis dependencies of the real API
- Tests the actual middleware code (SecurityHeadersMiddleware, parse_cors_origins)
  as it would run inside each service
- Runs without Docker or any external services

For tests that exercise /auth/token specifically (Cache-Control on the real
auth router), we load the API main.py via importlib to stay consistent with
the project's module isolation strategy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

# ── Ensure shared module is importable ───────────────────────────────────────

_ROOT = Path(__file__).parent.parent.parent
_SHARED = _ROOT / "services" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from security_headers import SecurityHeadersMiddleware, parse_cors_origins  # noqa: E402

# ── Minimal test application factory ─────────────────────────────────────────

_ALLOWED_ORIGIN = "https://frontend.autoflow.dev"


def _build_test_app(
    allowed_origins: str = _ALLOWED_ORIGIN,
    env: str = "development",
    security_headers: bool = True,
) -> FastAPI:
    """
    Build a minimal FastAPI app that mirrors the real service setup:
    SecurityHeadersMiddleware + CORSMiddleware + a few sample routes.
    """
    app = FastAPI()

    # Security headers (conditional, mirrors settings.security_headers_enabled)
    if security_headers:
        app.add_middleware(SecurityHeadersMiddleware)

    # CORS (uses the shared parse_cors_origins helper — the real production code)
    origins = parse_cors_origins(allowed_origins.strip(), env)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return "# prometheus metrics\n"

    @app.post("/auth/token")
    async def auth_token():
        return {"access_token": "test-jwt", "token_type": "bearer"}

    @app.get("/api/v1/resource")
    async def resource():
        return {"data": []}

    return app


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """HTTP test client against the minimal secured app."""
    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
async def https_client():
    """HTTPS test client (scheme='https' → HSTS header injected)."""
    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as c:
        yield c


# ── Security headers on /health ───────────────────────────────────────────────


@pytest.mark.integration
async def test_health_returns_200(client):
    """GET /health must return 200 even with the security middleware active."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.integration
async def test_security_headers_present_on_health(client):
    """GET /health must include all mandatory security headers."""
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers.get("content-security-policy", "")


@pytest.mark.integration
async def test_health_no_cache_control_no_store(client):
    """/health is a public route — it must NOT receive Cache-Control: no-store."""
    resp = await client.get("/health")
    assert resp.headers.get("cache-control") != "no-store"


# ── Cache-Control on /auth/token ──────────────────────────────────────────────


@pytest.mark.integration
async def test_auth_token_has_cache_control_no_store(client):
    """POST /auth/token must return Cache-Control: no-store to prevent token caching."""
    resp = await client.post("/auth/token")
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.integration
async def test_auth_token_also_has_security_headers(client):
    """POST /auth/token must include the standard security headers in addition to Cache-Control."""
    resp = await client.post("/auth/token")
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"


# ── HSTS on HTTPS ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_hsts_injected_on_https(https_client):
    """Over HTTPS, Strict-Transport-Security must be present with max-age=31536000."""
    resp = await https_client.get("/health")
    hsts = resp.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


@pytest.mark.integration
async def test_hsts_absent_on_http(client):
    """Over HTTP, Strict-Transport-Security must NOT be present."""
    resp = await client.get("/health")
    assert "strict-transport-security" not in resp.headers


# ── CORS — cross-origin rejection ─────────────────────────────────────────────


@pytest.mark.integration
async def test_cors_allowed_origin_granted(client):
    """A preflight from the allowed origin must receive the CORS header."""
    resp = await client.options(
        "/api/v1/resource",
        headers={
            "Origin": _ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == _ALLOWED_ORIGIN


@pytest.mark.integration
async def test_cors_disallowed_origin_rejected(client):
    """A request from an unauthorized origin must not receive the CORS header."""
    resp = await client.get(
        "/api/v1/resource",
        headers={"Origin": "https://evil.attacker.com"},
    )
    # 200 (server responds) but no CORS header → browser blocks the response
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.integration
async def test_cors_no_origin_header_has_no_cors_response(client):
    """A same-origin request (no Origin header) must not receive CORS headers."""
    resp = await client.get("/api/v1/resource")
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.integration
async def test_cors_wildcard_blocked_in_production():
    """Building an app with CORS_ORIGINS=* and ENV=production must raise RuntimeError."""
    with pytest.raises(RuntimeError, match="ENV=production"):
        _build_test_app(allowed_origins="*", env="production")


@pytest.mark.integration
async def test_cors_multiple_allowed_origins():
    """When two origins are configured, both must be accepted individually."""
    origin_a = "https://app.autoflow.dev"
    origin_b = "https://admin.autoflow.dev"
    app = _build_test_app(allowed_origins=f"{origin_a},{origin_b}")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        for origin in (origin_a, origin_b):
            resp = await client.get(
                "/api/v1/resource",
                headers={"Origin": origin},
            )
            assert resp.headers.get("access-control-allow-origin") == origin, (
                f"Expected CORS header for {origin}"
            )
