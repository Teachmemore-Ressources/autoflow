# Autoflow — tests/unit/test_notifications — Apache 2.0
"""Unit tests for services/api/notifications.py"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_API = Path(__file__).parent.parent.parent / "services" / "api"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(webhook_url: str = "", slack_webhook: str = "") -> MagicMock:
    s = MagicMock()
    s.notification_webhook_url = webhook_url
    s.notification_slack_webhook = slack_webhook
    return s


def _load_notif(alias: str, settings_mock: MagicMock):
    spec = importlib.util.spec_from_file_location(alias, _API / "notifications.py")
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec so @dataclass can resolve cls.__module__ via sys.modules
    with patch.dict("sys.modules", {alias: mod, "settings": MagicMock(settings=settings_mock)}):
        spec.loader.exec_module(mod)
    mod.settings = settings_mock
    mod._watched.clear()
    return mod


# ── register ──────────────────────────────────────────────────────────────────


def test_register_skips_when_no_targets():
    mod = _load_notif("notif_reg_skip", _make_settings())
    mod.register(job_id=1, callback_url="", metadata=None)
    assert 1 not in mod._watched


def test_register_with_callback_url():
    mod = _load_notif("notif_reg_cb", _make_settings())
    mod.register(job_id=2, callback_url="http://cb.example.com", metadata={"k": "v"})
    assert 2 in mod._watched
    assert mod._watched[2].callback_url == "http://cb.example.com"


def test_watched_count():
    mod = _load_notif("notif_count", _make_settings(webhook_url="http://x"))
    mod.register(job_id=10)
    mod.register(job_id=11)
    assert mod.watched_count() == 2


# ── collect_loop: CancelledError from _poll exits cleanly ─────────────────────


async def test_collect_loop_exits_on_cancelled_error():
    mod = _load_notif("notif_loop_cancel", _make_settings())
    mod._watched[1] = mod._WatchedJob(job_id=1, callback_url="http://cb")

    async def mock_poll(_http):
        raise asyncio.CancelledError

    with patch.object(mod, "_poll", mock_poll):
        # collect_loop catches CancelledError during _poll and returns cleanly
        await asyncio.wait_for(mod.collect_loop(MagicMock(), interval=0), timeout=2.0)


# ── collect_loop: non-cancel exception is logged, loop continues ──────────────


async def test_collect_loop_handles_poll_exception():
    mod = _load_notif("notif_loop_exc", _make_settings())
    mod._watched[1] = mod._WatchedJob(job_id=1)

    async def flaky_poll(_http):
        raise ConnectionError("redis down")

    async def mock_sleep(_interval):
        raise asyncio.CancelledError  # break the loop after first error

    with patch.object(mod, "_poll", flaky_poll):
        with patch("asyncio.sleep", mock_sleep):
            try:
                await mod.collect_loop(MagicMock(), interval=0)
            except asyncio.CancelledError:
                pass  # propagated from sleep — expected


# ── _poll: exception per-job is caught ────────────────────────────────────────


async def test_poll_http_error_handled():
    mod = _load_notif("notif_poll_err", _make_settings())
    mod._watched[1] = mod._WatchedJob(job_id=1)

    http = AsyncMock()
    http.get = AsyncMock(side_effect=ConnectionError("timeout"))

    await mod._poll(http)
    assert 1 in mod._watched  # job not removed — error, not completed


# ── _poll: terminal status fires notification ─────────────────────────────────


async def test_poll_fires_on_terminal_status():
    mod = _load_notif("notif_poll_fire", _make_settings())
    watched = mod._WatchedJob(job_id=7, callback_url="http://cb")
    mod._watched[7] = watched

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "successful", "summary_fields": {}}

    http = AsyncMock()
    http.get = AsyncMock(return_value=mock_response)

    with patch.object(mod, "_fire", AsyncMock()) as mock_fire:
        await mod._poll(http)

    mock_fire.assert_awaited_once()
    assert 7 not in mod._watched  # removed after completion


# ── _fire: webhook_url path ───────────────────────────────────────────────────


async def test_fire_posts_to_webhook_url():
    settings_mock = _make_settings(webhook_url="http://hook.example.com/cb")
    mod = _load_notif("notif_fire_hook", settings_mock)

    job = {"status": "successful", "summary_fields": {}}
    watched = mod._WatchedJob(job_id=1)

    with patch.object(mod, "_post_json", AsyncMock()) as mock_post:
        await mod._fire(job, watched)

    # Called once for the webhook URL (no slack, no callback_url)
    mock_post.assert_awaited_once_with(settings_mock.notification_webhook_url, mock_post.call_args[0][1])


# ── _fire: slack path ─────────────────────────────────────────────────────────


async def test_fire_calls_slack():
    settings_mock = _make_settings(slack_webhook="http://hooks.slack.com/T/B/abc")
    mod = _load_notif("notif_fire_slack", settings_mock)

    job = {"status": "failed", "summary_fields": {}}
    watched = mod._WatchedJob(job_id=2)

    with patch.object(mod, "_post_json", AsyncMock()):
        with patch.object(mod, "_post_slack", AsyncMock()) as mock_slack:
            await mod._fire(job, watched)

    mock_slack.assert_awaited_once()


# ── _fire: exception in gather is logged ─────────────────────────────────────


async def test_fire_exception_in_task_logged(caplog):
    settings_mock = _make_settings(webhook_url="http://hook.example.com/cb")
    mod = _load_notif("notif_fire_exc", settings_mock)

    job = {"status": "successful", "summary_fields": {}}
    watched = mod._WatchedJob(job_id=3, callback_url="http://per-job-cb")

    # Patch _post_json to raise — gather captures it via return_exceptions=True
    with patch.object(mod, "_post_json", AsyncMock(side_effect=RuntimeError("forced"))):
        with caplog.at_level(logging.WARNING):
            await mod._fire(job, watched)

    assert any("Notification task" in msg for msg in caplog.messages)


# ── _post_json ────────────────────────────────────────────────────────────────


async def test_post_json_success():
    mod = _load_notif("notif_pj_ok", _make_settings())

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await mod._post_json("http://example.com/hook", {"a": 1})

    mock_client.post.assert_awaited_once()


async def test_post_json_exception_is_swallowed(caplog):
    mod = _load_notif("notif_pj_err", _make_settings())

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level(logging.WARNING):
            await mod._post_json("http://example.com/hook", {})

    # Exception is caught — no uncaught exception, warning logged
    assert any("Webhook failed" in msg for msg in caplog.messages)


# ── _post_slack ───────────────────────────────────────────────────────────────


async def test_post_slack_successful_job_no_metadata():
    mod = _load_notif("notif_slack_ok", _make_settings())

    job = {
        "status": "successful",
        "elapsed": 45.0,
        "summary_fields": {"job_template": {"name": "Deploy Prod"}},
    }
    watched = mod._WatchedJob(job_id=42, metadata={})

    with patch.object(mod, "_post_json", AsyncMock()) as mock_post:
        await mod._post_slack("http://slack/hook", job, watched)

    mock_post.assert_awaited_once()
    payload = mock_post.call_args[0][1]
    assert "attachments" in payload
    assert "#2eb886" in str(payload)  # green for successful


async def test_post_slack_failed_job_with_metadata():
    mod = _load_notif("notif_slack_meta", _make_settings())

    job = {"status": "failed", "elapsed": 120.5, "summary_fields": {}}
    watched = mod._WatchedJob(job_id=99, metadata={"env": "prod", "app": "api"})

    with patch.object(mod, "_post_json", AsyncMock()) as mock_post:
        await mod._post_slack("http://slack/hook", job, watched)

    mock_post.assert_awaited_once()
    payload = mock_post.call_args[0][1]
    # Metadata block is included
    payload_str = str(payload)
    assert "Metadata" in payload_str
    assert "#e01e5a" in payload_str  # red for failed


# ── _fmt_dur ──────────────────────────────────────────────────────────────────


def test_fmt_dur_under_60s():
    mod = _load_notif("notif_fmt_s", _make_settings())
    assert mod._fmt_dur(45.0) == "45s"


def test_fmt_dur_minutes():
    mod = _load_notif("notif_fmt_m", _make_settings())
    result = mod._fmt_dur(90.0)
    assert "m" in result
    assert "1m" in result


def test_fmt_dur_hours():
    mod = _load_notif("notif_fmt_h", _make_settings())
    result = mod._fmt_dur(3700.0)
    assert "h" in result
    assert "1h" in result
