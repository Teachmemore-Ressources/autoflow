"""
Integration tests: API → AWX job → completion notification.

These tests exercise the notification pipeline inside services/api/:
  1. A job is registered for watching via ``notifications.register()``
  2. AWX stub reports the job as completed
  3. The notification module fires the callback URL
  4. The callback stub records the payload

All service modules are obtained through fixtures defined in conftest.py
so that there are no module-level sys.path changes here (avoiding collision
with event-engine modules that share the same file names).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_watched(api_notifications):
    """Reset the in-memory notifications watch store between tests."""
    api_notifications._watched.clear()
    yield
    api_notifications._watched.clear()


# ── Tests — notifications.register ───────────────────────────────────────────


@pytest.mark.integration
async def test_register_adds_job_to_watch_store(api_notifications, callback_stub):
    """register() with a callback URL adds the job to the watch store."""
    api_notifications.register(
        job_id=1,
        callback_url="http://callback-stub/callback",
        metadata={"env": "prod"},
    )
    assert api_notifications.watched_count() == 1


@pytest.mark.integration
async def test_no_register_without_targets(api_notifications):
    """register() is a no-op when no notification targets are configured."""
    # Temporarily clear all notification targets on the module's settings
    api_settings = api_notifications.settings
    original_webhook = api_settings.notification_webhook_url
    original_slack = api_settings.notification_slack_webhook

    object.__setattr__(api_settings, "notification_webhook_url", "")
    object.__setattr__(api_settings, "notification_slack_webhook", "")
    try:
        api_notifications.register(job_id=99)  # no callback_url, no global targets
        assert api_notifications.watched_count() == 0
    finally:
        object.__setattr__(api_settings, "notification_webhook_url", original_webhook)
        object.__setattr__(api_settings, "notification_slack_webhook", original_slack)


# ── Tests — notifications._poll ──────────────────────────────────────────────


@pytest.mark.integration
async def test_poll_fires_callback_when_job_completed(
    api_notifications, awx_stub_app, awx_stub, callback_stub_app, callback_stub
):
    """
    When a watched job reaches terminal status, the notification is POSTed
    to the per-job callback URL.
    """
    # Seed the AWX stub with a job and mark it successful
    launch_resp = await awx_stub.post(
        "/api/v2/job_templates/1/launch/",
        json={"extra_vars": {}},
        headers={"Authorization": "Bearer test"},
    )
    assert launch_resp.status_code == 201
    job_id = launch_resp.json()["id"]

    await awx_stub.put(f"/_test/jobs/{job_id}/status", params={"status": "successful"})

    # Register the job for notification
    api_notifications.register(
        job_id=job_id,
        callback_url="http://callback-stub/callback",
        metadata={"triggered_by": "integration-test"},
    )

    # Run one poll cycle — redirect HTTP POSTs to the in-process callback stub
    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        original_post = api_notifications._post_json

        async def _patched_post_json(url: str, payload: dict) -> None:
            async with AsyncClient(
                transport=ASGITransport(app=callback_stub_app),
                base_url="http://callback-stub",
            ) as cb:
                await cb.post("/callback", json=payload)

        api_notifications._post_json = _patched_post_json
        try:
            await api_notifications._poll(awx_http)
        finally:
            api_notifications._post_json = original_post

    # Verify the callback stub received the notification
    received = (await callback_stub.get("/_test/received")).json()
    assert len(received) == 1
    notif = received[0]
    assert notif["job_id"] == job_id
    assert notif["status"] == "successful"
    assert notif["metadata"]["triggered_by"] == "integration-test"


@pytest.mark.integration
async def test_poll_removes_job_after_terminal_status(api_notifications, awx_stub_app, awx_stub):
    """After a job reaches terminal status, it is removed from the watch store."""
    launch_resp = await awx_stub.post(
        "/api/v2/job_templates/1/launch/",
        json={"extra_vars": {}},
        headers={"Authorization": "Bearer test"},
    )
    job_id = launch_resp.json()["id"]
    await awx_stub.put(f"/_test/jobs/{job_id}/status", params={"status": "failed"})

    api_notifications.register(job_id=job_id, callback_url="http://irrelevant/cb")

    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        original_post = api_notifications._post_json

        # Async no-op (suppress the outgoing POST)
        async def _noop(url: str, payload: dict) -> None:
            pass

        api_notifications._post_json = _noop
        try:
            await api_notifications._poll(awx_http)
        finally:
            api_notifications._post_json = original_post

    assert api_notifications.watched_count() == 0


@pytest.mark.integration
async def test_poll_keeps_running_job_in_watch_store(api_notifications, awx_stub_app, awx_stub):
    """Jobs still in 'running' state are NOT removed from the watch store."""
    launch_resp = await awx_stub.post(
        "/api/v2/job_templates/1/launch/",
        json={"extra_vars": {}},
        headers={"Authorization": "Bearer test"},
    )
    job_id = launch_resp.json()["id"]
    # Do NOT mark as terminal — stays 'running'

    api_notifications.register(job_id=job_id, callback_url="http://irrelevant/cb")

    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        await api_notifications._poll(awx_http)

    assert api_notifications.watched_count() == 1  # still watching


@pytest.mark.integration
async def test_poll_handles_missing_job_gracefully(api_notifications, awx_stub_app, awx_stub):
    """If AWX returns 404 for a watched job, the poll does not raise."""
    # Register a job that doesn't exist in the stub
    api_notifications.register(job_id=9999, callback_url="http://irrelevant/cb")

    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        await api_notifications._poll(awx_http)  # must not raise

    # Job stays in watch store (AWX 404 is treated as a transient error)
    assert api_notifications.watched_count() == 1


@pytest.mark.integration
async def test_notification_payload_contains_required_fields(
    api_notifications, awx_stub_app, awx_stub, callback_stub_app, callback_stub
):
    """Notification payload contains all expected fields."""
    launch_resp = await awx_stub.post(
        "/api/v2/job_templates/1/launch/",
        json={"extra_vars": {"env": "staging"}},
        headers={"Authorization": "Bearer test"},
    )
    job_id = launch_resp.json()["id"]
    await awx_stub.put(f"/_test/jobs/{job_id}/status", params={"status": "successful"})

    api_notifications.register(
        job_id=job_id,
        callback_url="http://callback-stub/callback",
        metadata={"app": "api", "env": "staging"},
    )

    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        original_post = api_notifications._post_json

        async def _patched(url: str, payload: dict) -> None:
            async with AsyncClient(
                transport=ASGITransport(app=callback_stub_app),
                base_url="http://callback-stub",
            ) as cb:
                await cb.post("/callback", json=payload)

        api_notifications._post_json = _patched
        try:
            await api_notifications._poll(awx_http)
        finally:
            api_notifications._post_json = original_post

    received = (await callback_stub.get("/_test/received")).json()
    assert len(received) == 1
    notif = received[0]

    for field in ("job_id", "status", "failed", "metadata"):
        assert field in notif, f"missing field: {field}"

    assert notif["status"] == "successful"
    assert notif["failed"] is False
    assert notif["metadata"]["app"] == "api"


@pytest.mark.integration
async def test_failed_job_notification_sets_failed_true(
    api_notifications, awx_stub_app, awx_stub, callback_stub_app, callback_stub
):
    """When a job fails, the notification payload has failed=True."""
    launch_resp = await awx_stub.post(
        "/api/v2/job_templates/1/launch/",
        json={"extra_vars": {}},
        headers={"Authorization": "Bearer test"},
    )
    job_id = launch_resp.json()["id"]
    await awx_stub.put(f"/_test/jobs/{job_id}/status", params={"status": "failed"})

    api_notifications.register(
        job_id=job_id,
        callback_url="http://callback-stub/callback",
    )

    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as awx_http:
        original_post = api_notifications._post_json

        async def _patched(url: str, payload: dict) -> None:
            async with AsyncClient(
                transport=ASGITransport(app=callback_stub_app),
                base_url="http://callback-stub",
            ) as cb:
                await cb.post("/callback", json=payload)

        api_notifications._post_json = _patched
        try:
            await api_notifications._poll(awx_http)
        finally:
            api_notifications._post_json = original_post

    received = (await callback_stub.get("/_test/received")).json()
    assert len(received) == 1
    assert received[0]["status"] == "failed"
    assert received[0]["failed"] is True
