"""
Job Notifications
=================
Background task that polls AWX for terminal job status and fires callbacks.

Supports:
  • Per-job callback URL  (passed at watch-registration time)
  • Global webhook        (NOTIFICATION_WEBHOOK_URL env var)
  • Slack incoming webhook (NOTIFICATION_SLACK_WEBHOOK env var, Block Kit)

Usage:
    from notifications import register, collect_loop

    # Register a job to watch (call right after launching)
    register(job_id=42, callback_url="https://...", metadata={"env": "prod"})

    # Start the polling loop as a background task
    asyncio.create_task(collect_loop(http_client, interval=15))
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from settings import settings

logger = logging.getLogger("notifications")

# ── In-memory watch store ─────────────────────────────────────────────────────

@dataclass
class _WatchedJob:
    job_id:       int
    callback_url: str = ""
    metadata:     dict = field(default_factory=dict)

_watched: dict[int, _WatchedJob] = {}
_TERMINAL = frozenset({"successful", "failed", "error", "canceled"})


def register(
    job_id: int,
    callback_url: str = "",
    metadata: dict | None = None,
) -> None:
    """
    Register a job for completion notification.

    Silently skips registration when no notification targets are configured
    AND no per-job callback_url is provided.
    """
    if not any([
        callback_url,
        settings.notification_webhook_url,
        settings.notification_slack_webhook,
    ]):
        return

    _watched[job_id] = _WatchedJob(
        job_id=job_id,
        callback_url=callback_url,
        metadata=metadata or {},
    )
    logger.info("Watching job %d for completion notification", job_id)


def watched_count() -> int:
    """Number of jobs currently being watched."""
    return len(_watched)


# ── Polling loop ──────────────────────────────────────────────────────────────

async def collect_loop(http: httpx.AsyncClient, interval: int = 15) -> None:
    """
    Infinite loop: poll AWX every *interval* seconds for watched jobs.
    Fires notifications when a job reaches a terminal state.
    """
    logger.info("Job watcher started (poll_interval=%ds)", interval)
    while True:
        if _watched:
            try:
                await _poll(http)
            except asyncio.CancelledError:
                logger.info("Job watcher stopped")
                return
            except Exception as exc:
                logger.warning("Job watcher poll error: %s", exc)
        await asyncio.sleep(interval)


async def _poll(http: httpx.AsyncClient) -> None:
    completed: list[int] = []
    for job_id, watched in list(_watched.items()):
        try:
            r = await http.get(f"/api/v2/jobs/{job_id}/")
            if r.status_code == 200:
                job = r.json()
                if job.get("status") in _TERMINAL:
                    await _fire(job, watched)
                    completed.append(job_id)
        except Exception as exc:
            logger.debug("Poll error for job %d: %s", job_id, exc)
    for jid in completed:
        _watched.pop(jid, None)


# ── Notification dispatchers ──────────────────────────────────────────────────

async def _fire(job: dict, watched: _WatchedJob) -> None:
    jstatus = job.get("status", "unknown")
    payload = _build_payload(job, watched)
    logger.info(
        "Job %d finished — status=%s  firing notifications", watched.job_id, jstatus
    )

    tasks: list = []
    if watched.callback_url:
        tasks.append(_post_json(watched.callback_url, payload))
    if settings.notification_webhook_url:
        tasks.append(_post_json(settings.notification_webhook_url, payload))
    if settings.notification_slack_webhook:
        tasks.append(_post_slack(settings.notification_slack_webhook, job, watched))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning("Notification task %d raised: %s", i, r)


def _build_payload(job: dict, watched: _WatchedJob) -> dict:
    sf = job.get("summary_fields", {})
    return {
        "job_id":           watched.job_id,
        "status":           job.get("status"),
        "job_type":         job.get("job_type"),
        "name":             job.get("name"),
        "started":          job.get("started"),
        "finished":         job.get("finished"),
        "elapsed_seconds":  job.get("elapsed"),
        "failed":           job.get("failed"),
        "job_template":     sf.get("job_template", {}).get("name"),
        "launched_by":      sf.get("created_by", {}).get("username"),
        "metadata":         watched.metadata,
    }


async def _post_json(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
            logger.info("Webhook → %s  HTTP %d", url, r.status_code)
    except Exception as exc:
        logger.warning("Webhook failed (%s): %s", url, exc)


async def _post_slack(url: str, job: dict, watched: _WatchedJob) -> None:
    jstatus  = job.get("status", "unknown")
    color    = "#2eb886" if jstatus == "successful" else "#e01e5a"
    icon     = "✅" if jstatus == "successful" else "❌"
    template = job.get("summary_fields", {}).get("job_template", {}).get("name", "N/A")
    elapsed  = job.get("elapsed", 0)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{icon} AWX Job — {jstatus.capitalize()}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Job ID:*\n`#{watched.job_id}`"},
                {"type": "mrkdwn", "text": f"*Template:*\n{template}"},
                {"type": "mrkdwn", "text": f"*Status:*\n{jstatus}"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{_fmt_dur(elapsed)}"},
            ],
        },
    ]
    if watched.metadata:
        meta = "  ".join(f"`{k}={v}`" for k, v in watched.metadata.items())
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Metadata:* {meta}"},
        })

    await _post_json(url, {"attachments": [{"color": color, "blocks": blocks}]})


def _fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"
