"""
Background worker — processes the Redis pending and retry queues.

Architecture
------------
Two asyncio tasks run concurrently:

  _run_pending  — pops events from ``ee:queue:pending`` (new, never tried)
  _run_retry    — pops events from ``ee:queue:retry`` whose retry_at <= now

Each event is processed via the ``dispatch_fn`` closure injected at
construction time.  This closure captures the AWX client, rule engine
and dedup store from ``app.state``, so the worker has no direct
reference to the FastAPI application.

Concurrency is bounded by a semaphore (MAX_CONCURRENT) to prevent
thundering-herd if the queue has a large backlog.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from event_store import MAX_CONCURRENT, POLL_INTERVAL, EventStore

logger = logging.getLogger(__name__)

# Type alias for the dispatch function expected by the worker
DispatchFn = Callable[..., Awaitable[dict[str, Any]]]


class RetryWorker:
    """
    Processes events from the Redis pending + retry queues.

    Parameters
    ----------
    store :
        Initialised ``EventStore`` instance.
    dispatch_fn :
        Async callable ``(source, action, data, event_id) -> dict``.
        Must raise on failure so ``schedule_retry`` can be triggered.
    """

    def __init__(self, store: EventStore, dispatch_fn: DispatchFn) -> None:
        self._store       = store
        self._dispatch    = dispatch_fn
        self._sem         = asyncio.Semaphore(MAX_CONCURRENT)
        self._tasks: list[asyncio.Task] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._run_pending(), name="worker:pending"),
            asyncio.create_task(self._run_retry(),   name="worker:retry"),
        ]
        logger.info(
            "RetryWorker started (max_concurrent=%d poll_interval=%.1fs)",
            MAX_CONCURRENT, POLL_INTERVAL,
        )

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        logger.info("RetryWorker stopped")

    # ── Queue loops ────────────────────────────────────────────────────────────

    async def _run_pending(self) -> None:
        while True:
            try:
                event_ids = await self._store.pop_pending()
                for eid in event_ids:
                    asyncio.create_task(self._process(eid), name=f"dispatch:{eid[:8]}")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("RetryWorker pending-loop error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)

    async def _run_retry(self) -> None:
        while True:
            try:
                event_ids = await self._store.pop_due_retries()
                for eid in event_ids:
                    asyncio.create_task(self._process(eid), name=f"retry:{eid[:8]}")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("RetryWorker retry-loop error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)

    # ── Event processing ───────────────────────────────────────────────────────

    async def _process(self, event_id: str) -> None:
        async with self._sem:
            event = await self._store.get_event(event_id)
            if not event:
                logger.warning("Worker: event %s not found in store — skipping", event_id)
                return

            try:
                result = await self._dispatch(
                    source=event["source"],
                    action=event["action"],
                    data=event["data"],
                    event_id=event_id,
                )
                if result.get("status") == "deduplicated":
                    await self._store.mark_deduplicated(event_id)
                    logger.debug("Worker: event %s deduplicated", event_id)
                else:
                    await self._store.mark_completed(
                        event_id,
                        job_id=result.get("job_id"),
                    )
                    logger.info(
                        "Worker: event %s completed — job_id=%s template=%s",
                        event_id,
                        result.get("job_id"),
                        result.get("template_id"),
                    )

            except Exception as exc:
                logger.warning(
                    "Worker: event %s dispatch failed (%s) — scheduling retry",
                    event_id, exc,
                )
                await self._store.schedule_retry(event_id, str(exc))
