"""
Redis-backed event persistence, retry queue and dead-letter queue.

Queue architecture
------------------
  ee:event:{id}        STRING  — full event JSON; TTL managed per-status
  ee:queue:pending     SORTED SET  score=created_at_unix, member=event_id
  ee:queue:retry       SORTED SET  score=retry_at_unix,   member=event_id
  ee:dlq               LIST of event_ids (RPUSH on exhaustion)
  ee:dedup:{fp}        STRING "1" with TTL=dedup_ttl seconds

Failure policy
--------------
  attempt 1  → retry in  30 s
  attempt 2  → retry in  120 s
  attempt 3  → retry in  300 s
  attempt 4  → retry in  600 s
  attempt 5  → dead-letter queue (no more retries)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Redis key constants ────────────────────────────────────────────────────────
_PFX = "ee:"
_EVENT_KEY = _PFX + "event:{}"
_PENDING_Q = _PFX + "queue:pending"
_RETRY_Q = _PFX + "queue:retry"
_DLQ = _PFX + "dlq"
_DEDUP_KEY = _PFX + "dedup:{}"

# ── Retry schedule ─────────────────────────────────────────────────────────────
_MAX_ATTEMPTS = 5
_RETRY_DELAYS = [30, 120, 300, 600, 1200]  # seconds, indexed by attempt-1
_TTL_COMPLETED = 86_400  # 24 h  — keep completed events for audit
_TTL_DEAD = 86_400 * 7  # 7 days — DLQ events stay longer

# ── Worker tuning ──────────────────────────────────────────────────────────────
POLL_INTERVAL = 1.0  # seconds between queue polls
MAX_CONCURRENT = 10  # max simultaneous dispatches
PENDING_BATCH = 20  # events popped per poll cycle
RETRY_BATCH = 20


class EventStore:
    """
    Manages the full event lifecycle in Redis.

    All methods are coroutines; call them with ``await``.
    The Redis client must be a ``redis.asyncio.Redis`` instance.
    """

    def __init__(self, redis) -> None:
        self._r = redis

    # ── Ingestion ──────────────────────────────────────────────────────────────

    async def enqueue(
        self,
        source: str,
        action: str,
        data: dict[str, Any],
    ) -> str:
        """
        Persist a new event and add it to the pending queue.
        Returns the generated event_id (UUID4).
        """
        event_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        event: dict[str, Any] = {
            "id": event_id,
            "source": source,
            "action": action,
            "data": data,
            "received_at": now_iso,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "next_retry_at": None,
        }

        async with self._r.pipeline(transaction=True) as pipe:
            pipe.set(_EVENT_KEY.format(event_id), json.dumps(event))
            pipe.zadd(_PENDING_Q, {event_id: time.time()})
            await pipe.execute()

        return event_id

    # ── Queue consumers ────────────────────────────────────────────────────────

    async def pop_pending(self, limit: int = PENDING_BATCH) -> list[str]:
        """FIFO-pop up to `limit` events from the pending queue."""
        items = await self._r.zpopmin(_PENDING_Q, limit)
        return [(i[0].decode() if isinstance(i[0], bytes) else i[0]) for i in items]

    async def pop_due_retries(self, limit: int = RETRY_BATCH) -> list[str]:
        """Pop events from the retry queue whose scheduled time has arrived."""
        now = time.time()
        items = await self._r.zrangebyscore(_RETRY_Q, 0, now, start=0, num=limit)
        if not items:
            return []
        async with self._r.pipeline(transaction=True) as pipe:
            for item in items:
                pipe.zrem(_RETRY_Q, item)
            await pipe.execute()
        return [(i.decode() if isinstance(i, bytes) else i) for i in items]

    # ── State transitions ──────────────────────────────────────────────────────

    async def get_event(self, event_id: str) -> dict | None:
        raw = await self._r.get(_EVENT_KEY.format(event_id))
        return json.loads(raw) if raw else None

    async def mark_completed(self, event_id: str, job_id: Any = None) -> None:
        event = await self.get_event(event_id) or {}
        event.update({"status": "completed", "job_id": job_id})
        await self._r.set(
            _EVENT_KEY.format(event_id),
            json.dumps(event),
            ex=_TTL_COMPLETED,
        )

    async def mark_deduplicated(self, event_id: str) -> None:
        event = await self.get_event(event_id) or {}
        event["status"] = "deduplicated"
        await self._r.set(
            _EVENT_KEY.format(event_id),
            json.dumps(event),
            ex=_TTL_COMPLETED,
        )

    async def schedule_retry(self, event_id: str, error: str) -> None:
        """
        Increment the attempt counter and either:
        - schedule the next retry (exponential backoff), or
        - move the event to the dead-letter queue.
        """
        event = await self.get_event(event_id) or {}
        attempts = event.get("attempts", 0) + 1
        event["attempts"] = attempts
        event["last_error"] = error

        if attempts >= _MAX_ATTEMPTS:
            event["status"] = "dead"
            async with self._r.pipeline(transaction=True) as pipe:
                pipe.set(_EVENT_KEY.format(event_id), json.dumps(event), ex=_TTL_DEAD)
                pipe.rpush(_DLQ, event_id)
                await pipe.execute()
            logger.error(
                "Event %s moved to DLQ after %d attempts. Last error: %s",
                event_id,
                attempts,
                error,
            )
        else:
            delay = _RETRY_DELAYS[attempts - 1]
            retry_at = time.time() + delay
            event["status"] = "pending_retry"
            event["next_retry_at"] = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()

            async with self._r.pipeline(transaction=True) as pipe:
                pipe.set(_EVENT_KEY.format(event_id), json.dumps(event))
                pipe.zadd(_RETRY_Q, {event_id: retry_at})
                await pipe.execute()

            logger.warning(
                "Event %s retry #%d scheduled in %ds (error: %s)",
                event_id,
                attempts,
                delay,
                error,
            )

    # ── Dead-letter queue management ───────────────────────────────────────────

    async def dlq_list(self, offset: int = 0, limit: int = 50) -> list[dict]:
        ids = await self._r.lrange(_DLQ, offset, offset + limit - 1)
        events = []
        for raw_id in ids:
            eid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            event = await self.get_event(eid)
            if event:
                events.append(event)
        return events

    async def dlq_count(self) -> int:
        return await self._r.llen(_DLQ)

    async def dlq_requeue(self, event_id: str) -> bool:
        """
        Move an event from the DLQ back to the pending queue.
        Resets attempt counter so it gets a full retry budget.
        """
        event = await self.get_event(event_id)
        if not event:
            return False

        event["attempts"] = 0
        event["status"] = "pending"
        event["last_error"] = None
        event["next_retry_at"] = None

        async with self._r.pipeline(transaction=True) as pipe:
            pipe.set(_EVENT_KEY.format(event_id), json.dumps(event))
            pipe.lrem(_DLQ, 0, event_id)
            pipe.zadd(_PENDING_Q, {event_id: time.time()})
            await pipe.execute()

        logger.info("Event %s re-queued from DLQ", event_id)
        return True

    async def dlq_clear(self) -> int:
        count = await self._r.llen(_DLQ)
        await self._r.delete(_DLQ)
        return count

    # ── Queue statistics ───────────────────────────────────────────────────────

    async def queue_stats(self) -> dict:
        async with self._r.pipeline(transaction=False) as pipe:
            pipe.zcard(_PENDING_Q)
            pipe.zcard(_RETRY_Q)
            pipe.llen(_DLQ)
            pending, retry, dlq = await pipe.execute()
        return {"pending": pending, "retry": retry, "dlq": dlq}

    # ── Redis-backed deduplication ─────────────────────────────────────────────

    async def is_duplicate(self, fingerprint: str, ttl_seconds: int) -> bool:
        """
        Atomic check-and-register using Redis SET NX.

        Returns True  → event is a duplicate (key already existed)
        Returns False → event is new (key was just created with the given TTL)
        """
        result = await self._r.set(
            _DEDUP_KEY.format(fingerprint),
            "1",
            ex=ttl_seconds,
            nx=True,
        )
        return result is None  # None = NX condition not met = key already existed
