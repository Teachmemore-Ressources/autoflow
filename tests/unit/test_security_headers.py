# Autoflow — tests/unit/test_security_headers — Apache 2.0
"""
Unit tests for SecurityHeadersMiddleware.

Tests the shared middleware (services/shared/security_headers.py) in
isolation using a minimal FastAPI test application.
No external services or Docker containers required.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from security_headers import SecurityHeadersMiddleware

# ── Minimal test apps ─────────────────────────────────────────────────────────


def _make_app(enabled: bool = True) -> FastAPI:
    """Return a minimal FastAPI app with (or without) the security middleware."""
    app = FastAPI()

    if enabled:
        app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return "# prometheus\n"

    @app.get("/docs")
    async def docs():
        return {"docs": "here"}

    @app.post("/auth/token")
    async def auth_token():
        return {"access_token": "xxx", "token_type": "bearer"}

    @app.get("/auth/me")
    async def auth_me():
        return {"username": "admin"}

    @app.get("/awx/jobs")
    async def jobs():
        return []

    return app


@pytest.fixture
async def http_client():
    """HTTP client — simulates requests arriving over plain HTTP."""
    app = _make_app(enabled=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture
async def https_client():
    """HTTPS client — request.url.scheme == 'https' inside the middleware."""
    app = _make_app(enabled=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


@pytest.fixture
async def disabled_client():
    """Client for an app where SecurityHeadersMiddleware is NOT added."""
    app = _make_app(enabled=False)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


# ── Standard header presence ──────────────────────────────────────────────────


@pytest.mark.unit
async def test_x_content_type_options_present(http_client):
    """X-Content-Type-Options: nosniff must be set on every response."""
    resp = await http_client.get("/awx/jobs")
    assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.unit
async def test_x_frame_options_present(http_client):
    """X-Frame-Options: DENY must be set on every response."""
    resp = await http_client.get("/awx/jobs")
    assert resp.headers.get("x-frame-options") == "DENY"


@pytest.mark.unit
async def test_x_xss_protection_present(http_client):
    """X-XSS-Protection: 1; mode=block must be set on every response."""
    resp = await http_client.get("/awx/jobs")
    assert resp.headers.get("x-xss-protection") == "1; mode=block"


@pytest.mark.unit
async def test_referrer_policy_present(http_client):
    """Referrer-Policy: strict-origin-when-cross-origin must be set."""
    resp = await http_client.get("/awx/jobs")
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.unit
async def test_permissions_policy_present(http_client):
    """Permissions-Policy disabling geolocation, microphone, camera."""
    resp = await http_client.get("/awx/jobs")
    val = resp.headers.get("permissions-policy", "")
    assert "geolocation=()" in val
    assert "microphone=()" in val
    assert "camera=()" in val


@pytest.mark.unit
async def test_content_security_policy_present(http_client):
    """CSP must include default-src 'self' and frame-ancestors 'none'."""
    resp = await http_client.get("/awx/jobs")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# ── HSTS — conditional on HTTPS scheme ───────────────────────────────────────


@pytest.mark.unit
async def test_hsts_absent_on_http(http_client):
    """Strict-Transport-Security must NOT be injected on plain HTTP requests."""
    resp = await http_client.get("/awx/jobs")
    assert "strict-transport-security" not in resp.headers


@pytest.mark.unit
async def test_hsts_present_on_https(https_client):
    """Strict-Transport-Security must be set when the request arrives over HTTPS."""
    resp = await https_client.get("/awx/jobs")
    hsts = resp.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


# ── Cache-Control — auth routes only ─────────────────────────────────────────


@pytest.mark.unit
async def test_cache_control_no_store_on_auth_token(http_client):
    """POST /auth/token must receive Cache-Control: no-store."""
    resp = await http_client.post("/auth/token")
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.unit
async def test_cache_control_no_store_on_auth_me(http_client):
    """GET /auth/me must receive Cache-Control: no-store (auth prefix)."""
    resp = await http_client.get("/auth/me")
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.unit
async def test_cache_control_absent_on_health(http_client):
    """GET /health must NOT receive Cache-Control: no-store (public route)."""
    resp = await http_client.get("/health")
    assert resp.headers.get("cache-control") != "no-store"


@pytest.mark.unit
async def test_cache_control_absent_on_metrics(http_client):
    """GET /metrics must NOT receive Cache-Control: no-store (public route)."""
    resp = await http_client.get("/metrics")
    assert resp.headers.get("cache-control") != "no-store"


@pytest.mark.unit
async def test_cache_control_absent_on_docs(http_client):
    """GET /docs must NOT receive Cache-Control: no-store (public route)."""
    resp = await http_client.get("/docs")
    assert resp.headers.get("cache-control") != "no-store"


@pytest.mark.unit
async def test_cache_control_absent_on_regular_route(http_client):
    """GET /awx/jobs must NOT receive Cache-Control: no-store."""
    resp = await http_client.get("/awx/jobs")
    assert resp.headers.get("cache-control") != "no-store"


# ── Middleware disabled ───────────────────────────────────────────────────────


@pytest.mark.unit
async def test_headers_absent_when_middleware_disabled(disabled_client):
    """When the middleware is NOT added, no security headers must be present."""
    resp = await disabled_client.get("/awx/jobs")
    assert "x-content-type-options" not in resp.headers
    assert "x-frame-options" not in resp.headers
    assert "x-xss-protection" not in resp.headers
    assert "referrer-policy" not in resp.headers
    assert "permissions-policy" not in resp.headers
    assert "content-security-policy" not in resp.headers
    assert "strict-transport-security" not in resp.headers


# ── All headers on a single response ─────────────────────────────────────────


@pytest.mark.unit
async def test_all_security_headers_present_on_https(https_client):
    """Smoke test: every expected security header is present over HTTPS."""
    resp = await https_client.get("/awx/jobs")
    expected_headers = [
        "x-content-type-options",
        "x-frame-options",
        "x-xss-protection",
        "referrer-policy",
        "permissions-policy",
        "content-security-policy",
        "strict-transport-security",
    ]
    for header in expected_headers:
        assert header in resp.headers, f"Missing header: {header}"
