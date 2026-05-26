# Autoflow — tests/unit/test_cors_settings — Apache 2.0
"""
Unit tests for CORS configuration validation and settings parsing.

Tests ``parse_cors_origins()`` from services/shared/security_headers.py and
the CORS-related fields of each service's Settings class.
No external services or Docker containers required.
"""
from __future__ import annotations

import logging

import pytest
from security_headers import parse_cors_origins

# ── parse_cors_origins — validation logic ─────────────────────────────────────

@pytest.mark.unit
def test_wildcard_raises_in_production():
    """CORS_ORIGINS=* with ENV=production must raise RuntimeError."""
    with pytest.raises(RuntimeError, match="CORS_ORIGINS=\\* is not allowed when ENV=production"):
        parse_cors_origins("*", "production")


@pytest.mark.unit
def test_wildcard_allowed_in_development():
    """CORS_ORIGINS=* with ENV=development must succeed (only logs a warning)."""
    result = parse_cors_origins("*", "development")
    # "*" is returned as a single-element list — CORSMiddleware interprets it
    assert result == ["*"]


@pytest.mark.unit
def test_wildcard_env_check_is_case_insensitive():
    """ENV=Production (mixed case) must still trigger the RuntimeError."""
    with pytest.raises(RuntimeError):
        parse_cors_origins("*", "Production")


@pytest.mark.unit
def test_empty_origins_returns_empty_list(caplog):
    """Empty CORS_ORIGINS must return [] and log a WARNING."""
    with caplog.at_level(logging.WARNING, logger="autoflow.security"):
        result = parse_cors_origins("", "development")
    assert result == []
    assert any("CORS not configured" in msg for msg in caplog.messages)


@pytest.mark.unit
def test_whitespace_only_origins_returns_empty_list():
    """CORS_ORIGINS with only whitespace must be treated as empty."""
    result = parse_cors_origins("   ", "development")
    assert result == []


@pytest.mark.unit
def test_wildcard_logs_warning(caplog):
    """CORS_ORIGINS=* must log a WARNING even in development."""
    with caplog.at_level(logging.WARNING, logger="autoflow.security"):
        parse_cors_origins("*", "development")
    assert any("CORS_ORIGINS=*" in msg for msg in caplog.messages)


# ── parse_cors_origins — parsing ──────────────────────────────────────────────

@pytest.mark.unit
def test_single_origin():
    """Single origin is returned as a one-element list."""
    result = parse_cors_origins("https://app.example.com", "development")
    assert result == ["https://app.example.com"]


@pytest.mark.unit
def test_multiple_origins_comma_separated():
    """Comma-separated origins are split and stripped correctly."""
    result = parse_cors_origins(
        "https://app.example.com,https://admin.example.com",
        "development",
    )
    assert result == ["https://app.example.com", "https://admin.example.com"]


@pytest.mark.unit
def test_origins_with_extra_whitespace():
    """Whitespace around commas is stripped."""
    result = parse_cors_origins(
        " https://a.com , https://b.com , https://c.com ",
        "development",
    )
    assert result == ["https://a.com", "https://b.com", "https://c.com"]


@pytest.mark.unit
def test_trailing_comma_is_ignored():
    """A trailing comma must not produce an empty string in the list."""
    result = parse_cors_origins("https://app.example.com,", "development")
    assert result == ["https://app.example.com"]


@pytest.mark.unit
def test_duplicate_commas_are_ignored():
    """Consecutive commas must not produce empty strings."""
    result = parse_cors_origins("https://a.com,,https://b.com", "development")
    assert result == ["https://a.com", "https://b.com"]


# ── Settings class — CORS and env fields ─────────────────────────────────────
#
# We test each service's Settings in isolation by instantiating it with
# explicit kwargs (which have higher priority than env vars in pydantic-settings).

@pytest.mark.unit
def test_api_settings_cors_defaults():
    """API Settings: cors_origins defaults to '' and env to 'development'."""
    import importlib.util
    from pathlib import Path

    _api = Path(__file__).parent.parent.parent / "services" / "api"

    # Load API settings under a unique alias to avoid polluting sys.modules['settings']
    # — the same alias trick used in tests/integration/conftest.py.
    spec = importlib.util.spec_from_file_location("api_settings_cors_test", _api / "settings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    Settings = mod.Settings

    # pydantic-settings: constructor kwargs have highest priority (override env vars)
    s = Settings(
        awx_admin_password="test-password",
        api_secret_key="test-api-secret-32chars-long-xxxxxxxx",
        cors_origins="",
        env="development",
        security_headers_enabled=True,
    )

    assert s.cors_origins == ""
    assert s.env == "development"
    assert s.security_headers_enabled is True
    assert s.cors_allow_credentials is False
    assert s.cors_allow_methods == ["GET", "POST", "PUT", "DELETE"]
    assert s.cors_allow_headers == ["Authorization", "Content-Type"]


@pytest.mark.unit
def test_api_settings_cors_production_guard(monkeypatch):
    """parse_cors_origins raises RuntimeError for '*' in production."""
    # Direct test of the function — no Settings instantiation needed
    with pytest.raises(RuntimeError):
        parse_cors_origins("*", "production")


@pytest.mark.unit
def test_event_engine_settings_cors_fields():
    """Event Engine Settings: CORS fields have expected defaults."""
    import importlib.util
    from pathlib import Path

    _ee = Path(__file__).parent.parent.parent / "services" / "event-engine"

    # Load under a unique alias to avoid polluting sys.modules['settings']
    spec = importlib.util.spec_from_file_location("ee_settings_cors_test", _ee / "settings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    Settings = mod.Settings

    s = Settings(
        awx_token="test-token",
        awx_job_template_id=1,
        cors_origins="https://example.com",
        env="development",
    )
    assert s.cors_origins == "https://example.com"
    assert s.cors_allow_credentials is False
    assert s.env == "development"
    assert s.security_headers_enabled is True


# ── CORS middleware behaviour — minimal app ───────────────────────────────────
#
# These tests verify that the CORSMiddleware + parse_cors_origins combination
# correctly accepts allowed origins and rejects disallowed ones.

@pytest.mark.unit
async def test_cors_allowed_origin_returns_header():
    """A request from an allowed origin must receive Access-Control-Allow-Origin."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    origins = parse_cors_origins("https://allowed.example.com", "development")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/ping",
            headers={"Origin": "https://allowed.example.com"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://allowed.example.com"


@pytest.mark.unit
async def test_cors_disallowed_origin_no_header():
    """A request from a disallowed origin must NOT receive Access-Control-Allow-Origin."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    origins = parse_cors_origins("https://allowed.example.com", "development")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/ping",
            headers={"Origin": "https://evil.example.com"},
        )

    assert resp.status_code == 200
    # No CORS header → browser blocks the response
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.unit
async def test_cors_empty_origins_rejects_all():
    """When cors_origins is empty, no origin gets the CORS header."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    origins = parse_cors_origins("", "development")  # returns []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/ping",
            headers={"Origin": "https://any.example.com"},
        )

    assert "access-control-allow-origin" not in resp.headers
