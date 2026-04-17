"""
Event deduplication.

Prevents the same event from triggering multiple jobs within a
configurable time window (default 60 s).

Uses an in-process TTL cache (no Redis dependency).  For multi-replica
deployments, swap the store for a Redis-backed implementation.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


class DedupStore:
    """Thread-safe in-memory TTL cache for event fingerprints."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, float] = {}   # fingerprint → expiry timestamp

    # ── Public API ────────────────────────────────────────────────────────────

    def is_duplicate(self, source: str, action: str, data: dict[str, Any]) -> bool:
        """
        Return True if an identical event was seen within the TTL window.

        Side-effect: if NOT a duplicate, register the fingerprint so that
        subsequent calls within the window return True.
        """
        self._evict()
        fp = self._fingerprint(source, action, data)
        if fp in self._store:
            return True
        self._store[fp] = time.monotonic() + self._ttl
        return False

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        self._evict()
        return len(self._store)

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(source: str, action: str, data: dict[str, Any]) -> str:
        payload = json.dumps(
            {"source": source, "action": action, "data": data},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _evict(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]
