"""
E2E tests: full webhook → Event Engine → AWX stub → notification path.

Topology (docker-compose.test.yml):
  event-engine   → listens on :8001 (external :8001)
  awx-stub       → listens on :8052 (external :8052)
  callback-stub  → listens on :9999 (external :9999)

The event engine's AWX_URL points to awx-stub:8052 (internal docker network).
NOTIFICATION_WEBHOOK_URL in the event engine is NOT set here — notifications
are tested via the per-job callback_url field in the API service.

For the E2E notification path we use the API service, not the event engine.

Run:
    make test-e2e
    # or
    EE_URL=http://localhost:8001 AWX_STUB_URL=http://localhost:8052 \
    CALLBACK_URL=http://localhost:9999 pytest -m e2e -v tests/e2e/
"""
from __future__ import annotations

import asyncio

import pytest


# ── Smoke tests ───────────────────────────────────────────────────────────────

@pytest.mark.e2e
async def test_event_engine_health(ee):
    """Event engine responds on /health."""
    resp = await ee.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.e2e
async def test_awx_stub_ping(awx_stub):
    """AWX stub responds on its ping endpoint."""
    resp = await awx_stub.get("/api/v2/ping/")
    assert resp.status_code == 200
    assert "version" in resp.json()


@pytest.mark.e2e
async def test_callback_stub_ready(callback):
    """Callback stub is up and starts with no records."""
    count_resp = await callback.get("/_test/count")
    assert count_resp.status_code == 200
    assert count_resp.json()["count"] == 0


# ── Webhook → AWX job ─────────────────────────────────────────────────────────

@pytest.mark.e2e
async def test_github_push_creates_awx_job(ee, awx_stub):
    """GitHub push webhook → event engine → AWX job launched."""
    payload = {
        "ref":         "refs/heads/main",
        "after":       "cafebabe",
        "before":      "00000000",
        "repository":  {"full_name": "acme/app", "name": "app",
                         "html_url": "https://github.com/acme/app"},
        "sender":      {"login": "alice"},
        "head_commit": {"id": "cafebabe", "message": "fix: prod bug",
                         "author": {"name": "Alice"}},
    }
    resp = await ee.post(
        "/webhook/github",
        json=payload,
        headers={"X-GitHub-Event": "push"},
    )
    assert resp.status_code in (200, 202), resp.text

    # AWX stub should have one launch
    launches = (await awx_stub.get("/_test/launches")).json()
    assert len(launches) >= 1
    latest = launches[-1]
    assert latest["template_id"] >= 1
    ev = latest["extra_vars"]
    assert ev["event_source"] == "github"
    assert ev["event_action"] == "push"


@pytest.mark.e2e
async def test_alertmanager_firing_creates_awx_job(ee, awx_stub):
    """Alertmanager firing webhook → event engine → AWX job launched."""
    payload = {
        "status":      "firing",
        "receiver":    "autoflow-webhook",
        "externalURL": "https://alertmanager.example.com",
        "groupLabels": {"alertname": "DiskUsage"},
        "alerts": [
            {
                "labels":      {"alertname": "DiskUsage", "severity": "warning",
                                  "instance": "storage-01"},
                "annotations": {"summary": "Disk 90% full"},
                "startsAt":    "2026-04-26T11:00:00Z",
                "endsAt":      "0001-01-01T00:00:00Z",
            }
        ],
    }
    resp = await ee.post("/webhook/alertmanager", json=payload)
    assert resp.status_code in (200, 202), resp.text

    launches = (await awx_stub.get("/_test/launches")).json()
    assert len(launches) >= 1
    ev = launches[-1]["extra_vars"]
    assert ev["event_source"] == "alertmanager"
    assert ev["event_action"] == "firing"


@pytest.mark.e2e
async def test_generic_event_creates_awx_job(ee, awx_stub):
    """Generic /event → event engine → AWX job launched."""
    resp = await ee.post(
        "/event",
        json={"action": "e2e-test", "source": "pytest", "data": {"run": "ci"}},
    )
    assert resp.status_code in (200, 202), resp.text

    launches = (await awx_stub.get("/_test/launches")).json()
    assert any(
        l["extra_vars"].get("event_action") == "e2e-test" for l in launches
    )


# ── Deduplication (live) ──────────────────────────────────────────────────────

@pytest.mark.e2e
async def test_duplicate_event_suppressed(ee, awx_stub):
    """Identical events sent twice → second is deduplicated."""
    payload = {
        "action": "dedup-e2e",
        "source": "pytest",
        "data":   {"unique_key": "dedup-e2e-test-run"},
    }
    r1 = await ee.post("/event", json=payload)
    r2 = await ee.post("/event", json=payload)

    assert r1.status_code in (200, 202)
    assert r2.json()["status"] == "deduplicated"

    launches = (await awx_stub.get("/_test/launches")).json()
    dedup_launches = [
        l for l in launches
        if l["extra_vars"].get("event_action") == "dedup-e2e"
    ]
    assert len(dedup_launches) == 1


# ── Full notification flow ────────────────────────────────────────────────────

@pytest.mark.e2e
async def test_job_completion_sends_notification(awx_stub, callback):
    """
    Full stack: launch a job → mark it done in AWX stub →
    notification sent to callback stub.

    This test directly uses the AWX stub's launch endpoint and then drives
    the notification by setting the job to terminal status.
    The event engine is NOT involved here — this exercises the API service's
    job watcher loop.

    Prerequisites:
      API_URL must be set and the API service must be pointing at awx-stub.
    """
    import os
    api_url = os.getenv("API_URL")
    if not api_url:
        pytest.skip("API_URL not set — skipping notification E2E test")

    from httpx import AsyncClient

    callback_receive_url = os.getenv("CALLBACK_RECEIVE_URL", "http://callback-stub:9999/callback")

    async with AsyncClient(base_url=api_url, timeout=15.0) as api:
        # Authenticate
        token_resp = await api.post(
            "/auth/token",
            json={"username": os.getenv("API_USERNAME", "admin"),
                  "password": os.getenv("API_SECRET_KEY", "test-api-secret-32chars-long-xxxxxxxx")},
        )
        assert token_resp.status_code == 200, token_resp.text
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Launch a job via the API with our callback URL
        launch_resp = await api.post(
            "/awx/job-templates/1/launch",
            json={
                "extra_vars": {"e2e": True},
                "callback_url": callback_receive_url,
                "notify_metadata": {"test": "e2e-notification"},
            },
            headers=headers,
        )
        assert launch_resp.status_code in (200, 201), launch_resp.text
        job_id = launch_resp.json()["id"]

    # Mark the job as successful in the AWX stub
    await awx_stub.put(f"/_test/jobs/{job_id}/status", params={"status": "successful"})

    # Wait for the job watcher to detect completion and fire the callback
    # (JOB_WATCHER_INTERVAL=1 in test environment)
    for _ in range(10):
        await asyncio.sleep(1)
        count_resp = await callback.get("/_test/count")
        if count_resp.json()["count"] >= 1:
            break

    received = (await callback.get("/_test/received")).json()
    assert len(received) >= 1

    notif = next(
        (r for r in received if r.get("job_id") == job_id), None
    )
    assert notif is not None, f"No notification for job {job_id}: {received}"
    assert notif["status"] == "successful"
    assert notif["metadata"]["test"] == "e2e-notification"
