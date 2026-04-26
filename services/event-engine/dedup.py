"""
Event deduplication.

Prevents the same event from triggering multiple jobs within a
configurable time window (default 60 s).

Two backends are supported and selected automatically:

Redis backend  (preferred)
    When a ``redis.asyncio.Redis`` instance is injected via
    ``DedupStore.set_redis()``, dedup state is stored in Redis with
    native TTL keys.  State survives container restarts and is shared
    across replicas.

In-memory backend  (fallback)
    Used when Redis is not available.  State is local to the process
    and lost on restart.

Both backends expose the same ``is_duplicate()`` interface; the only
difference is that the Redis path is async while the in-memory path is
synchronous — callers must ``await`` either way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class DedupStore:
    """
    Event fingerprint cache with optional Redis backend.

    Usage
    -----
    store = DedupStore(ttl_seconds=60)
    store.set_redis(redis_client)          # call after Redis is connected

    is_dup = await store.is_duplicate(source, action, data)
    """

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl    = ttl_seconds
        self._redis  = None
        self._mem: dict[str, float] = {}   # fingerprint → expiry (monotonic)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_redis(self, redis) -> None:
        """Inject a ``redis.asyncio.Redis`` client to enable the Redis backend."""
        self._redis = redis
        logger.info("DedupStore: Redis backend activated (ttl=%ds)", self._ttl)

    async def is_duplicate(
        self,
        source: str,
        action: str,
        data: dict[str, Any],
    ) -> bool:
        """
        Return True if an identical event was seen within the TTL window.

        Side-effect: if NOT a duplicate, registers the fingerprint so
        that subsequent identical calls within the window return True.
        """
        if self._ttl <= 0:
            return False

        fp = self._fingerprint(source, action, data)

        if self._redis is not None:
            return await self._redis_check(fp)
        return self._mem_check(fp)

    def clear(self) -> None:
        """Flush in-memory cache.  Redis keys expire naturally via TTL."""
        self._mem.clear()

    def size(self) -> int:
        """Return the number of active in-memory entries (Redis size not counted)."""
        self._evict()
        return len(self._mem)

    # ── Redis backend ──────────────────────────────────────────────────────────

    async def _redis_check(self, fingerprint: str) -> bool:
        """
        Atomic check-and-set: ``SET key 1 EX ttl NX``.

        Returns True  → key already existed → duplicate
        Returns False → key was set → not a duplicate
        """
        try:
            result = await self._redis.set(
                f"ee:dedup:{fingerprint}",
                "1",
                ex=self._ttl,
                nx=True,
            )
            return result is None   # None = NX not satisfied = key existed
        except Exception as exc:
            # Degrade gracefully to in-memory on Redis error
            logger.warning("DedupStore Redis error, falling back to in-memory: %s", exc)
            return self._mem_check(fingerprint)

    # ── In-memory backend ──────────────────────────────────────────────────────

    def _mem_check(self, fingerprint: str) -> bool:
        self._evict()
        if fingerprint in self._mem:
            return True
        self._mem[fingerprint] = time.monotonic() + self._ttl
        return False

    def _evict(self) -> None:
        now     = time.monotonic()
        expired = [k for k, exp in self._mem.items() if exp <= now]
        for k in expired:
            del self._mem[k]

    # ── Fingerprint ────────────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(source: str, action: str, data: dict[str, Any]) -> str:
        payload = json.dumps(
            {"source": source, "action": action, "data": data},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()
