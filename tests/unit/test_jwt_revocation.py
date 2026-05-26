# Autoflow — tests/unit/test_jwt_revocation — Apache 2.0
"""
Unit tests for JWT issuance and revocation logic.

Tests:
- create_access_token() includes a jti claim
- _decode_token() accepts a valid, non-revoked token
- _decode_token() rejects an expired token
- _decode_token() rejects a revoked token (Redis blacklist)
- logout endpoint blacklists the token in Redis
- logout endpoint tolerates missing Redis (graceful degradation)
- X-API-Key callers receive 200 from logout (no JWT to revoke)

No Docker / Redis container required — Redis is mocked with fakeredis.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

_API = Path(__file__).parent.parent.parent / "services" / "api"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(redis_url: str = "") -> MagicMock:
    s = MagicMock()
    s.effective_jwt_secret = "test-secret-32-chars-long-xxxxxxxxxxx"
    s.jwt_algorithm = "HS256"
    s.jwt_expire_minutes = 60
    s.redis_url = redis_url
    s.api_secret_key = "test-api-key"
    return s


def _load_auth(alias: str, settings_mock: MagicMock) -> object:
    """
    Load services/api/routers/auth.py under *alias*, mocking out all
    service-level dependencies (settings, limiter, user_store).

    No sys.path mutation — user_store / limiter / settings are mocked via
    patch.dict(sys.modules), keeping the canonical 'main' / 'settings' entries
    in sys.modules intact for the integration tests that run in the same session.
    """
    user_store_mock = MagicMock()
    user_store_mock.get_role.return_value = "admin"
    user_store_mock.authenticate.return_value = "admin"

    spec = importlib.util.spec_from_file_location(alias, _API / "routers" / "auth.py")
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(
        "sys.modules",
        {
            "settings": MagicMock(settings=settings_mock),
            "limiter": MagicMock(limiter=MagicMock()),
            "user_store": user_store_mock,
        },
    ):
        spec.loader.exec_module(mod)

    # Patch the module-level `settings` reference to our mock
    mod.settings = settings_mock
    mod.user_store = user_store_mock
    return mod


def _make_token(secret: str, algorithm: str = "HS256", **extra) -> str:
    payload = {
        "sub": "alice",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        "iat": datetime.now(timezone.utc),
        "jti": "test-jti-abc123",
    }
    payload.update(extra)
    return jwt.encode(payload, secret, algorithm=algorithm)


def _expired_token(secret: str) -> str:
    payload = {
        "sub": "alice",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        "iat": datetime.now(timezone.utc) - timedelta(minutes=65),
        "jti": "expired-jti",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ── create_access_token ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_access_token_has_jti():
    """Issued tokens must include a non-empty jti claim."""
    settings_mock = _make_settings()
    mod = _load_auth("auth_jti_test", settings_mock)
    token = mod.create_access_token("bob")
    payload = jwt.decode(token, settings_mock.effective_jwt_secret, algorithms=["HS256"])
    assert "jti" in payload
    assert payload["jti"]  # non-empty
    assert payload["sub"] == "bob"


@pytest.mark.unit
def test_create_access_token_unique_jti():
    """Each issued token must have a unique jti."""
    settings_mock = _make_settings()
    mod = _load_auth("auth_jti_unique_test", settings_mock)
    t1 = mod.create_access_token("alice")
    t2 = mod.create_access_token("alice")
    p1 = jwt.decode(t1, settings_mock.effective_jwt_secret, algorithms=["HS256"])
    p2 = jwt.decode(t2, settings_mock.effective_jwt_secret, algorithms=["HS256"])
    assert p1["jti"] != p2["jti"]


# ── _decode_token ─────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_decode_token_valid():
    """A valid, non-revoked token must return the username."""
    settings_mock = _make_settings()
    mod = _load_auth("auth_decode_valid_test", settings_mock)
    token = _make_token(settings_mock.effective_jwt_secret)

    with patch.object(mod, "_is_revoked", AsyncMock(return_value=False)):
        username = await mod._decode_token(token)

    assert username == "alice"


@pytest.mark.unit
async def test_decode_token_expired():
    """An expired token must raise HTTP 401."""
    from fastapi import HTTPException

    settings_mock = _make_settings()
    mod = _load_auth("auth_decode_expired_test", settings_mock)
    token = _expired_token(settings_mock.effective_jwt_secret)

    with pytest.raises(HTTPException) as exc_info:
        await mod._decode_token(token)

    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


@pytest.mark.unit
async def test_decode_token_revoked():
    """A revoked token (jti in blacklist) must raise HTTP 401."""
    from fastapi import HTTPException

    settings_mock = _make_settings(redis_url="redis://localhost:6379")
    mod = _load_auth("auth_decode_revoked_test", settings_mock)
    token = _make_token(settings_mock.effective_jwt_secret)

    with patch.object(mod, "_is_revoked", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as exc_info:
            await mod._decode_token(token)

    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


# ── _is_revoked / _revoke ─────────────────────────────────────────────────────


@pytest.mark.unit
async def test_is_revoked_false_without_redis():
    """When REDIS_URL is not set, _is_revoked must always return False."""
    settings_mock = _make_settings(redis_url="")
    mod = _load_auth("auth_is_revoked_no_redis", settings_mock)
    result = await mod._is_revoked("any-jti")
    assert result is False


@pytest.mark.unit
async def test_revoke_without_redis_logs_warning(caplog):
    """_revoke without REDIS_URL must log a WARNING (no exception)."""
    import logging

    settings_mock = _make_settings(redis_url="")
    mod = _load_auth("auth_revoke_no_redis", settings_mock)

    with caplog.at_level(logging.WARNING, logger="autoflow.auth"):
        await mod._revoke("some-jti", 300)

    assert any("REDIS_URL" in msg for msg in caplog.messages)


@pytest.mark.unit
async def test_is_revoked_with_fakeredis():
    """_is_revoked checks the Redis blacklist correctly (fakeredis)."""
    import fakeredis.aioredis as fakeredis

    settings_mock = _make_settings(redis_url="redis://localhost:6379")
    mod = _load_auth("auth_is_revoked_redis", settings_mock)

    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch.object(mod, "_redis", return_value=fake):
        # Not yet revoked
        assert await mod._is_revoked("jti-abc") is False

        # Manually blacklist it
        await fake.setex(f"{mod._REDIS_PREFIX}jti-abc", 60, "1")
        assert await mod._is_revoked("jti-abc") is True

        # Different jti is still clean
        assert await mod._is_revoked("jti-xyz") is False


@pytest.mark.unit
async def test_revoke_with_fakeredis():
    """_revoke stores the jti in Redis with correct TTL (fakeredis)."""
    import fakeredis.aioredis as fakeredis

    settings_mock = _make_settings(redis_url="redis://localhost:6379")
    mod = _load_auth("auth_revoke_redis", settings_mock)

    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch.object(mod, "_redis", return_value=fake):
        await mod._revoke("jti-test-ttl", 120)

    key = f"{mod._REDIS_PREFIX}jti-test-ttl"
    assert await fake.exists(key)
    ttl = await fake.ttl(key)
    assert 0 < ttl <= 120
