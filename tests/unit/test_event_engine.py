# Autoflow — tests/unit/test_event_engine — Apache 2.0
"""
Unit tests for services/event-engine/main.py.

Strategy
--------
The FastAPI app object is imported directly (event-engine is on sys.path via
the unit conftest, and environment variables are set by the root conftest).

App state is set manually on each test so the lifespan is never started:
no background workers, no Redis connections, no APScheduler.

HTTP calls go through httpx.AsyncClient with ASGITransport — same pattern
used by integration/conftest.py's ee_client fixture.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

import json as _json

import main as ee_main
from awx_client import AWXError
from main import EventPayload, _dispatch, _try_connect_redis, require_admin_token

_ADMIN = {"Authorization": "Bearer test-admin-token"}


# ── State helpers ─────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.queue_stats = AsyncMock(return_value={"pending": 1, "retry": 0, "dlq": 0})
    store.enqueue = AsyncMock(return_value="test-evt-id")
    store.dlq_list = AsyncMock(return_value=[{"id": "dlq-1", "status": "dead"}])
    store.dlq_count = AsyncMock(return_value=1)
    store.dlq_requeue = AsyncMock(return_value=True)
    store.dlq_clear = AsyncMock(return_value=2)
    store.get_event = AsyncMock(return_value={"id": "evt-1", "status": "completed"})
    return store


def _setup_state(store=None, is_dup: bool = False):
    """Set app.state so route handlers don't hit the real lifespan objects."""
    ee_main.app.state.event_store = store if store is not None else _mock_store()
    ee_main.app.state.dedup = MagicMock(
        is_duplicate=AsyncMock(return_value=is_dup),
        size=MagicMock(return_value=0),
        _redis=None,
        clear=MagicMock(),
    )
    ee_main.app.state.rules = MagicMock(
        _rules=[],
        _default_id=1,
        resolve=MagicMock(return_value=(1, {})),
        reload=MagicMock(return_value=0),
    )
    ee_main.app.state.awx = MagicMock(
        launch_job=AsyncMock(return_value={"id": 42, "url": "/jobs/42/"})
    )
    ee_main.app.state.scheduler = MagicMock(
        loaded_count=2,
        jobs=MagicMock(return_value=[]),
        reload=MagicMock(return_value=2),
    )
    ee_main.app.state.retry_worker = None


@pytest.fixture
async def http():
    """AsyncClient with app state pre-configured — lifespan not triggered."""
    _setup_state()
    async with AsyncClient(
        transport=ASGITransport(app=ee_main.app), base_url="http://testserver"
    ) as client:
        yield client


# ── require_admin_token ───────────────────────────────────────────────────────


def test_require_admin_token_disabled():
    """Returns 503 when ADMIN_TOKEN env var is not set."""
    with patch.object(ee_main.settings, "admin_token", ""):
        with pytest.raises(HTTPException) as exc:
            require_admin_token(credentials=None)
    assert exc.value.status_code == 503


def test_require_admin_token_missing_credentials():
    """Missing Bearer header → 401 when token IS configured."""
    with pytest.raises(HTTPException) as exc:
        require_admin_token(credentials=None)
    assert exc.value.status_code == 401


def test_require_admin_token_wrong_token():
    creds = MagicMock()
    creds.credentials = "wrong-token"
    with pytest.raises(HTTPException) as exc:
        require_admin_token(credentials=creds)
    assert exc.value.status_code == 401


def test_require_admin_token_valid():
    creds = MagicMock()
    creds.credentials = "test-admin-token"
    require_admin_token(credentials=creds)  # must not raise


# ── EventPayload validation ───────────────────────────────────────────────────


def test_event_payload_empty_action_raises():
    with pytest.raises(Exception):
        EventPayload(action="", source="ci")


def test_event_payload_whitespace_action_raises():
    with pytest.raises(Exception):
        EventPayload(action="   ", source="ci")


def test_event_payload_valid():
    p = EventPayload(action="deploy", source="ci", data={"env": "prod"})
    assert p.action == "deploy"


# ── _try_connect_redis ────────────────────────────────────────────────────────


async def test_try_connect_redis_no_url():
    with patch.object(ee_main.settings, "redis_url", ""):
        result = await _try_connect_redis()
    assert result is None


async def test_try_connect_redis_connection_fails():
    with patch.object(ee_main.settings, "redis_url", "redis://localhost:6379"):
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=ConnectionError("refused"))
            mock_from_url.return_value = mock_client
            result = await _try_connect_redis()
    assert result is None


async def test_try_connect_redis_success():
    with patch.object(ee_main.settings, "redis_url", "redis://localhost:6379"):
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_from_url.return_value = mock_client
            result = await _try_connect_redis()
    assert result is mock_client


# ── GET /health ───────────────────────────────────────────────────────────────


async def test_health_with_store(http):
    resp = await http.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["persistence"] is True
    assert "queue" in data


async def test_health_without_store(http):
    ee_main.app.state.event_store = None
    resp = await http.get("/health")
    assert resp.status_code == 200
    assert resp.json()["persistence"] is False


# ── _dispatch: persistent path (lines 338-364) ───────────────────────────────
# slowapi wraps receive_event in a way that hides the EventPayload signature
# from FastAPI's param parser — test _dispatch directly to cover those lines.


async def _make_request(store=None, is_dup: bool = False):
    """Return a mock Request with app.state pre-configured."""
    _setup_state(store=store, is_dup=is_dup)
    req = MagicMock()
    req.app = ee_main.app
    return req


async def test_dispatch_persistent_queued():
    req = await _make_request()
    resp = await _dispatch(req, "ci", "deploy", {})
    assert resp.status_code == 202
    body = _json.loads(resp.body)
    assert body["status"] == "queued"
    assert body["event_id"] == "test-evt-id"


async def test_dispatch_persistent_deduplicated():
    req = await _make_request(is_dup=True)
    resp = await _dispatch(req, "ci", "deploy", {})
    assert resp.status_code == 200
    assert _json.loads(resp.body)["status"] == "deduplicated"


async def test_dispatch_fallback_no_store():
    _setup_state()
    ee_main.app.state.event_store = None
    req = MagicMock()
    req.app = ee_main.app
    resp = await _dispatch(req, "ci", "deploy", {})
    assert resp.status_code == 202


async def test_dispatch_fallback_awx_error():
    _setup_state()
    ee_main.app.state.event_store = None
    ee_main.app.state.awx.launch_job = AsyncMock(side_effect=AWXError(502, "AWX down"))
    req = MagicMock()
    req.app = ee_main.app
    with pytest.raises(HTTPException) as exc_info:
        await _dispatch(req, "ci", "deploy", {})
    assert exc_info.value.status_code == 502


async def test_dispatch_fallback_generic_error():
    _setup_state()
    ee_main.app.state.event_store = None
    ee_main.app.state.awx.launch_job = AsyncMock(side_effect=RuntimeError("unexpected"))
    req = MagicMock()
    req.app = ee_main.app
    with pytest.raises(HTTPException) as exc_info:
        await _dispatch(req, "ci", "deploy", {})
    assert exc_info.value.status_code == 503


# ── POST /webhook/github ──────────────────────────────────────────────────────


async def test_webhook_github_no_secret(http):
    resp = await http.post(
        "/webhook/github",
        json={"action": "push", "repository": {"full_name": "org/repo"}},
        headers={"X-GitHub-Event": "push"},
    )
    assert resp.status_code in (200, 202)


async def test_webhook_github_missing_signature(http):
    with patch.object(ee_main.settings, "github_webhook_secret", "my-secret"):
        resp = await http.post(
            "/webhook/github",
            json={"action": "push"},
            headers={"X-GitHub-Event": "push"},
        )
    assert resp.status_code == 401


async def test_webhook_github_wrong_signature(http):
    with patch.object(ee_main.settings, "github_webhook_secret", "my-secret"):
        resp = await http.post(
            "/webhook/github",
            content=b'{"action":"push"}',
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": "sha256=badhash",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 403


async def test_webhook_github_valid_signature(http):
    import hashlib
    import hmac as _hmac

    secret = "test-sig-secret"
    body = b'{"action":"push","repository":{"full_name":"org/repo"}}'
    sig = "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(ee_main.settings, "github_webhook_secret", secret):
        resp = await http.post(
            "/webhook/github",
            content=body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code in (200, 202)


# ── POST /webhook/alertmanager ────────────────────────────────────────────────


async def test_webhook_alertmanager(http):
    resp = await http.post(
        "/webhook/alertmanager",
        json={
            "alerts": [
                {
                    "labels": {"alertname": "DiskFull", "severity": "critical"},
                    "status": "firing",
                }
            ]
        },
    )
    assert resp.status_code in (200, 202)


async def test_webhook_alertmanager_invalid_json(http):
    resp = await http.post(
        "/webhook/alertmanager",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# ── Admin — Rules ─────────────────────────────────────────────────────────────


async def test_list_rules(http):
    resp = await http.get("/admin/rules", headers=_ADMIN)
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert data["count"] == 0


async def test_reload_rules(http):
    resp = await http.post("/admin/rules/reload", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["status"] == "reloaded"


# ── Admin — Dedup ─────────────────────────────────────────────────────────────


async def test_dedup_stats(http):
    resp = await http.get("/admin/dedup/stats", headers=_ADMIN)
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "backend" in data


async def test_dedup_clear(http):
    resp = await http.post("/admin/dedup/clear", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"


# ── Admin — Schedules ─────────────────────────────────────────────────────────


async def test_list_schedules(http):
    resp = await http.get("/admin/schedules", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_reload_schedules(http):
    resp = await http.post("/admin/schedules/reload", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["status"] == "reloaded"


# ── Admin — Queue & DLQ ───────────────────────────────────────────────────────


async def test_queue_stats(http):
    resp = await http.get("/admin/queue/stats", headers=_ADMIN)
    assert resp.status_code == 200
    data = resp.json()
    assert "pending" in data


async def test_queue_stats_no_store(http):
    """_require_store raises 503 when event_store is None."""
    ee_main.app.state.event_store = None
    resp = await http.get("/admin/queue/stats", headers=_ADMIN)
    assert resp.status_code == 503


async def test_dlq_list(http):
    resp = await http.get("/admin/dlq", headers=_ADMIN)
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert data["total"] == 1


async def test_dlq_requeue_success(http):
    resp = await http.post("/admin/dlq/test-evt-id/requeue", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["status"] == "requeued"


async def test_dlq_requeue_not_found(http):
    ee_main.app.state.event_store.dlq_requeue = AsyncMock(return_value=False)
    resp = await http.post("/admin/dlq/no-such-id/requeue", headers=_ADMIN)
    assert resp.status_code == 404


async def test_dlq_clear(http):
    resp = await http.delete("/admin/dlq", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["removed"] == 2


# ── Admin — Events ────────────────────────────────────────────────────────────


async def test_get_event(http):
    resp = await http.get("/admin/events/evt-1", headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["id"] == "evt-1"


async def test_get_event_not_found(http):
    ee_main.app.state.event_store.get_event = AsyncMock(return_value=None)
    resp = await http.get("/admin/events/no-such-id", headers=_ADMIN)
    assert resp.status_code == 404


# ── Admin token enforcement ───────────────────────────────────────────────────


async def test_admin_endpoint_requires_token(http):
    resp = await http.get("/admin/rules")  # no Authorization header
    assert resp.status_code == 401
