# Autoflow — tests/unit/test_auth_extras — Apache 2.0
"""
Additional unit tests for services/api/routers/auth.py — covering routes
and helpers not reached by test_jwt_revocation.py.

Reuses the _load_auth / _make_settings / _make_token helpers.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

_API = Path(__file__).parent.parent.parent / "services" / "api"


# ── Helpers (same pattern as test_jwt_revocation.py) ─────────────────────────


def _make_settings(redis_url: str = "") -> MagicMock:
    s = MagicMock()
    s.effective_jwt_secret = "test-secret-32-chars-long-xxxxxxxxxxx"
    s.jwt_algorithm = "HS256"
    s.jwt_expire_minutes = 60
    s.redis_url = redis_url
    s.api_secret_key = "test-api-key"
    return s


def _load_auth(alias: str, settings_mock: MagicMock):
    user_store_mock = MagicMock()
    user_store_mock.get_role.return_value = "admin"
    user_store_mock.authenticate.return_value = "admin"

    # limiter.limit must be a passthrough decorator so @limiter.limit("5/minute")
    # on `login` doesn't replace the function with a MagicMock at load time.
    limiter_mock = MagicMock()
    limiter_mock.limit.side_effect = lambda _rate: (lambda f: f)

    spec = importlib.util.spec_from_file_location(alias, _API / "routers" / "auth.py")
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(
        "sys.modules",
        {
            "settings": MagicMock(settings=settings_mock),
            "limiter": MagicMock(limiter=limiter_mock),
            "user_store": user_store_mock,
        },
    ):
        spec.loader.exec_module(mod)

    mod.settings = settings_mock
    mod.user_store = user_store_mock
    return mod


def _make_token(secret: str, **extra) -> str:
    payload = {
        "sub": "alice",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        "iat": datetime.now(timezone.utc),
        "jti": "test-jti-abc",
    }
    payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


# ── _redis(): lazy initialisation ────────────────────────────────────────────


def test_redis_returns_none_when_no_url():
    settings_mock = _make_settings(redis_url="")
    mod = _load_auth("auth_redis_none", settings_mock)
    assert mod._redis() is None


def test_redis_creates_client_when_url_set():
    settings_mock = _make_settings(redis_url="redis://localhost:6379")
    mod = _load_auth("auth_redis_create", settings_mock)
    mod._redis_client = None

    import redis.asyncio as aioredis

    fake_client = MagicMock()
    with patch.object(aioredis, "from_url", return_value=fake_client):
        result = mod._redis()

    assert result is fake_client
    assert mod._redis_client is fake_client


def test_redis_returns_cached_client():
    """Second call returns the already-initialised client without re-importing."""
    settings_mock = _make_settings(redis_url="redis://localhost:6379")
    mod = _load_auth("auth_redis_cached", settings_mock)

    cached = MagicMock()
    mod._redis_client = cached

    result = mod._redis()
    assert result is cached


# ── _decode_token: InvalidTokenError path ────────────────────────────────────


async def test_decode_token_invalid_raises_401():
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_decode_inv", settings_mock)

    with pytest.raises(HTTPException) as exc:
        await mod._decode_token("not.a.valid.token")

    assert exc.value.status_code == 401
    assert "Invalid token" in exc.value.detail


# ── require_auth: Bearer token and unauthenticated paths ─────────────────────


async def test_require_auth_valid_bearer():
    settings_mock = _make_settings()
    mod = _load_auth("auth_req_bearer", settings_mock)

    with patch.object(mod, "_decode_token", AsyncMock(return_value="alice")):
        result = await mod.require_auth(api_key=None, token="some.jwt.token")

    assert result == "alice"


async def test_require_auth_no_credentials_raises_401():
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_req_noauth", settings_mock)

    with pytest.raises(HTTPException) as exc:
        await mod.require_auth(api_key=None, token=None)

    assert exc.value.status_code == 401


async def test_require_auth_invalid_api_key_raises_403():
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_req_badkey", settings_mock)

    with pytest.raises(HTTPException) as exc:
        await mod.require_auth(api_key="wrong-key", token=None)

    assert exc.value.status_code == 403


# ── require_write_access: viewer is rejected ─────────────────────────────────


async def test_require_write_access_viewer_rejected():
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_write_viewer", settings_mock)
    mod.user_store.get_role.return_value = "viewer"

    with pytest.raises(HTTPException) as exc:
        await mod.require_write_access(username="viewer-user")

    assert exc.value.status_code == 403


async def test_require_write_access_operator_allowed():
    settings_mock = _make_settings()
    mod = _load_auth("auth_write_operator", settings_mock)
    mod.user_store.get_role.return_value = "operator"

    result = await mod.require_write_access(username="op-user")
    assert result == "op-user"


# ── login: invalid credentials ────────────────────────────────────────────────


async def test_login_invalid_credentials_raises_401():
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_login_bad", settings_mock)
    mod.user_store.authenticate.return_value = None

    form_data = MagicMock()
    form_data.username = "alice"
    form_data.password = "wrong"

    with pytest.raises(HTTPException) as exc:
        await mod.login(MagicMock(), form_data)

    assert exc.value.status_code == 401


async def test_login_valid_returns_token():
    settings_mock = _make_settings()
    mod = _load_auth("auth_login_ok", settings_mock)
    mod.user_store.authenticate.return_value = "admin"

    form_data = MagicMock()
    form_data.username = "admin"
    form_data.password = "correct-pass"

    result = await mod.login(MagicMock(), form_data)

    assert "access_token" in result
    assert result["token_type"] == "bearer"
    assert result["username"] == "admin"
    assert result["role"] == "admin"


# ── refresh_token ─────────────────────────────────────────────────────────────


async def test_refresh_token_issues_new_token():
    settings_mock = _make_settings()
    mod = _load_auth("auth_refresh", settings_mock)

    result = await mod.refresh_token(username="alice")

    assert "access_token" in result
    assert result["token_type"] == "bearer"
    assert result["username"] == "alice"


# ── me ────────────────────────────────────────────────────────────────────────


async def test_me_returns_user_info():
    settings_mock = _make_settings()
    mod = _load_auth("auth_me", settings_mock)
    mod.user_store.get_role.return_value = "operator"

    result = await mod.me(username="alice")

    assert result["username"] == "alice"
    assert result["role"] == "operator"
    assert "jwt_algorithm" in result


async def test_me_api_key_caller_defaults_to_admin():
    settings_mock = _make_settings()
    mod = _load_auth("auth_me_apikey", settings_mock)
    mod.user_store.get_role.return_value = None  # API-key caller → no user record

    result = await mod.me(username="admin")
    assert result["role"] == "admin"


# ── logout ────────────────────────────────────────────────────────────────────


async def test_logout_with_valid_token_revokes():
    settings_mock = _make_settings()
    mod = _load_auth("auth_logout_ok", settings_mock)

    token = mod.create_access_token("alice")

    with patch.object(mod, "_revoke", AsyncMock()) as mock_revoke:
        result = await mod.logout(token=token, username="alice")

    mock_revoke.assert_awaited_once()
    assert result == {"status": "logged_out", "username": "alice"}


async def test_logout_no_token_is_noop():
    settings_mock = _make_settings()
    mod = _load_auth("auth_logout_none", settings_mock)

    with patch.object(mod, "_revoke", AsyncMock()) as mock_revoke:
        result = await mod.logout(token=None, username="alice")

    mock_revoke.assert_not_awaited()
    assert result["status"] == "logged_out"


async def test_logout_invalid_token_does_not_raise():
    settings_mock = _make_settings()
    mod = _load_auth("auth_logout_invalid", settings_mock)

    # InvalidTokenError must be swallowed gracefully
    result = await mod.logout(token="not.a.valid.token", username="alice")

    assert result["status"] == "logged_out"
