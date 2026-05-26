"""
AWX Job Metrics
===============
Background task that polls AWX /api/v2/jobs/ and exposes Prometheus gauges.

All gauges are registered with prometheus_client and will appear automatically
on the existing /metrics endpoint exposed by prometheus-fastapi-instrumentator.

Metrics
-------
  awx_jobs_total{status}   – Historical job count by terminal / active status
  awx_running_jobs         – Jobs currently executing
  awx_pending_jobs         – Jobs waiting to be dispatched (pending + waiting + new)
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from prometheus_client import Gauge

logger = logging.getLogger("awx_metrics")

# ── Prometheus gauges ─────────────────────────────────────────────────────────

_awx_jobs = Gauge(
    "awx_jobs_total",
    "AWX job count by status",
    ["status"],
)
_awx_running = Gauge("awx_running_jobs", "AWX jobs currently executing")
_awx_pending = Gauge("awx_pending_jobs", "AWX jobs waiting to start (pending + waiting + new)")

# Pre-initialise all label combinations so Grafana can query them even before
# the first successful poll.
for _s in ("successful", "failed", "error", "canceled", "running", "pending", "waiting", "new"):
    _awx_jobs.labels(status=_s)

_TERMINAL = ("successful", "failed", "error", "canceled")
_ACTIVE = ("running",)
_WAITING = ("pending", "waiting", "new")


# ── Collector loop ────────────────────────────────────────────────────────────


async def collect_loop(http: httpx.AsyncClient, interval: int = 60) -> None:
    """
    Infinite loop: refresh AWX job gauges every *interval* seconds.

    Started as an asyncio.Task from the app lifespan; cancelled on shutdown.
    """
    logger.info("AWX metrics collector started (interval=%ds)", interval)

    while True:
        try:
            await _poll(http)
        except asyncio.CancelledError:
            logger.info("AWX metrics collector stopped")
            return
        except Exception as exc:
            logger.warning("AWX metrics poll failed: %s", exc)

        await asyncio.sleep(interval)


async def _poll(http: httpx.AsyncClient) -> None:
    """Single poll cycle: fetch job counts from AWX and update gauges."""
    running_total = 0
    pending_total = 0

    for status in _TERMINAL:
        count = await _fetch_count(http, status)
        if count is not None:
            _awx_jobs.labels(status=status).set(count)

    for status in _ACTIVE:
        count = await _fetch_count(http, status)
        if count is not None:
            _awx_jobs.labels(status=status).set(count)
            running_total += count

    for status in _WAITING:
        count = await _fetch_count(http, status)
        if count is not None:
            _awx_jobs.labels(status=status).set(count)
            pending_total += count

    _awx_running.set(running_total)
    _awx_pending.set(pending_total)
    logger.debug(
        "AWX metrics updated — running=%d  pending=%d",
        running_total,
        pending_total,
    )


async def _fetch_count(http: httpx.AsyncClient, status: str) -> int | None:
    """Return the job count for *status*, or None on error."""
    try:
        r = await http.get(f"/api/v2/jobs/?status={status}&page_size=1")
        if r.status_code == 200:
            return int(r.json().get("count", 0))
    except Exception as exc:
        logger.debug("Failed to fetch count for status=%s: %s", status, exc)
    return None
