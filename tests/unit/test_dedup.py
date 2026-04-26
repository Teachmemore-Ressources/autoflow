"""Unit tests for the deduplication store (services/event-engine/dedup.py)."""
import asyncio
import time

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _store(ttl: int = 60):
    from dedup import DedupStore
    return DedupStore(ttl_seconds=ttl)


# ── In-memory backend ─────────────────────────────────────────────────────────

async def test_first_call_is_not_duplicate():
    store = _store()
    assert not await store.is_duplicate("github", "push", {"ref": "main"})


async def test_second_identical_call_is_duplicate():
    store = _store()
    data = {"ref": "refs/heads/main", "sha": "abc123"}
    assert not await store.is_duplicate("github", "push", data)
    assert     await store.is_duplicate("github", "push", data)


async def test_different_action_is_not_duplicate():
    store = _store()
    await store.is_duplicate("github", "push",  {"ref": "main"})
    assert not await store.is_duplicate("github", "merge", {"ref": "main"})


async def test_different_source_is_not_duplicate():
    store = _store()
    await store.is_duplicate("github",       "push", {"ref": "main"})
    assert not await store.is_duplicate("alertmanager", "push", {"ref": "main"})


async def test_different_data_is_not_duplicate():
    store = _store()
    await store.is_duplicate("github", "push", {"ref": "refs/heads/main"})
    assert not await store.is_duplicate("github", "push", {"ref": "refs/heads/feature"})


async def test_ttl_zero_never_deduplicates():
    store = _store(ttl=0)
    await store.is_duplicate("github", "push", {"ref": "main"})
    # Same call with TTL=0 is NEVER deduplicated
    assert not await store.is_duplicate("github", "push", {"ref": "main"})


async def test_expired_entry_is_not_duplicate():
    store = _store(ttl=1)
    await store.is_duplicate("github", "push", {"ref": "main"})
    # Wait for TTL to expire
    await asyncio.sleep(1.05)
    assert not await store.is_duplicate("github", "push", {"ref": "main"})


async def test_clear_removes_all_entries():
    store = _store()
    await store.is_duplicate("github", "push", {"a": 1})
    await store.is_duplicate("ci",     "deploy", {"b": 2})
    assert store.size() == 2
    store.clear()
    assert store.size() == 0
    # Entries are gone — next call is fresh
    assert not await store.is_duplicate("github", "push", {"a": 1})


async def test_size_counts_active_entries():
    store = _store()
    assert store.size() == 0
    await store.is_duplicate("s1", "a1", {})
    assert store.size() == 1
    await store.is_duplicate("s2", "a2", {})
    assert store.size() == 2


async def test_size_evicts_expired_entries():
    store = _store(ttl=1)
    await store.is_duplicate("s1", "a1", {})
    assert store.size() == 1
    await asyncio.sleep(1.05)
    assert store.size() == 0   # eviction triggered by size()


async def test_fingerprint_is_deterministic():
    """Same inputs always produce the same fingerprint."""
    from dedup import DedupStore
    fp1 = DedupStore._fingerprint("github", "push", {"ref": "main", "sha": "abc"})
    fp2 = DedupStore._fingerprint("github", "push", {"sha": "abc", "ref": "main"})
    assert fp1 == fp2   # sort_keys=True in json.dumps


async def test_nested_data_fingerprint():
    """Nested data structures are correctly fingerprinted."""
    store = _store()
    data1 = {"pr": {"number": 42, "title": "fix"}}
    data2 = {"pr": {"number": 42, "title": "fix"}}
    await store.is_duplicate("github", "pr", data1)
    assert await store.is_duplicate("github", "pr", data2)


# ── Redis backend (fakeredis) ─────────────────────────────────────────────────

@pytest.fixture
async def redis_store():
    """DedupStore with a fakeredis backend."""
    import fakeredis.aioredis as aioredis
    from dedup import DedupStore

    redis = aioredis.FakeRedis()
    store = DedupStore(ttl_seconds=60)
    store.set_redis(redis)
    yield store
    await redis.aclose()


async def test_redis_first_call_not_duplicate(redis_store):
    assert not await redis_store.is_duplicate("github", "push", {"ref": "main"})


async def test_redis_second_call_is_duplicate(redis_store):
    data = {"ref": "refs/heads/main", "sha": "def456"}
    assert not await redis_store.is_duplicate("github", "push", data)
    assert     await redis_store.is_duplicate("github", "push", data)


async def test_redis_different_action_not_duplicate(redis_store):
    await redis_store.is_duplicate("alertmanager", "firing",   {"alertname": "HighCPU"})
    assert not await redis_store.is_duplicate("alertmanager", "resolved", {"alertname": "HighCPU"})


async def test_redis_backend_attribute_set(redis_store):
    assert redis_store._redis is not None


async def test_redis_ttl_expires():
    """Keys set with EX=1 should expire naturally in Redis."""
    import fakeredis.aioredis as aioredis
    from dedup import DedupStore

    redis = aioredis.FakeRedis()
    store = DedupStore(ttl_seconds=1)
    store.set_redis(redis)

    await store.is_duplicate("ci", "deploy", {"env": "prod"})
    # Check key exists
    assert await redis.exists("ee:dedup:" + DedupStore._fingerprint("ci", "deploy", {"env": "prod"}))

    await asyncio.sleep(1.05)
    # Key should have expired
    fp = DedupStore._fingerprint("ci", "deploy", {"env": "prod"})
    assert not await redis.exists(f"ee:dedup:{fp}")

    await redis.aclose()


async def test_redis_fallback_on_error():
    """When Redis raises, the store falls back to the in-memory path."""
    from unittest.mock import AsyncMock, MagicMock
    from dedup import DedupStore

    # Make Redis.set raise an exception
    bad_redis = MagicMock()
    bad_redis.set = AsyncMock(side_effect=ConnectionError("redis down"))

    store = DedupStore(ttl_seconds=60)
    store.set_redis(bad_redis)

    # Should not raise; falls back to in-memory
    assert not await store.is_duplicate("github", "push", {"ref": "main"})
    # In-memory state was written, so second call IS a duplicate
    assert     await store.is_duplicate("github", "push", {"ref": "main"})
