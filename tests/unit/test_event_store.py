"""Unit tests for services/event-engine/event_store.py (EventStore + Redis key helpers)."""
from __future__ import annotations

import json
import time

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    import fakeredis.aioredis as aioredis
    from event_store import EventStore

    redis = aioredis.FakeRedis()
    es = EventStore(redis)
    yield es
    await redis.aclose()


# ── enqueue ───────────────────────────────────────────────────────────────────

async def test_enqueue_returns_uuid(store):
    eid = await store.enqueue("github", "push", {"ref": "main"})
    assert isinstance(eid, str) and len(eid) == 36


async def test_enqueue_stores_event(store):
    eid = await store.enqueue("ci", "deploy", {"env": "prod"})
    event = await store.get_event(eid)
    assert event is not None
    assert event["source"] == "ci"
    assert event["action"] == "deploy"
    assert event["status"] == "pending"
    assert event["attempts"] == 0


async def test_enqueue_adds_to_pending_queue(store):
    eid = await store.enqueue("s", "a", {})
    pending = await store.pop_pending()
    assert eid in pending


# ── pop_pending ───────────────────────────────────────────────────────────────

async def test_pop_pending_empty(store):
    result = await store.pop_pending()
    assert result == []


async def test_pop_pending_respects_limit(store):
    for i in range(5):
        await store.enqueue("s", f"a{i}", {})
    result = await store.pop_pending(limit=3)
    assert len(result) == 3


async def test_pop_pending_removes_from_queue(store):
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    pending_again = await store.pop_pending()
    assert eid not in pending_again


# ── pop_due_retries ───────────────────────────────────────────────────────────

async def test_pop_due_retries_empty(store):
    result = await store.pop_due_retries()
    assert result == []


async def test_pop_due_retries_returns_past_due(store):
    from event_store import _RETRY_Q
    eid = "test-retry-id"
    await store._r.zadd(_RETRY_Q, {eid: time.time() - 10})
    result = await store.pop_due_retries()
    assert eid in result


async def test_pop_due_retries_excludes_future(store):
    from event_store import _RETRY_Q
    eid = "future-id"
    await store._r.zadd(_RETRY_Q, {eid: time.time() + 9999})
    result = await store.pop_due_retries()
    assert eid not in result


async def test_pop_due_retries_removes_from_queue(store):
    from event_store import _RETRY_Q
    eid = "consumed-id"
    await store._r.zadd(_RETRY_Q, {eid: time.time() - 1})
    await store.pop_due_retries()
    score = await store._r.zscore(_RETRY_Q, eid)
    assert score is None


# ── get_event ─────────────────────────────────────────────────────────────────

async def test_get_event_not_found(store):
    result = await store.get_event("nonexistent-id")
    assert result is None


# ── mark_completed ────────────────────────────────────────────────────────────

async def test_mark_completed_updates_status(store):
    eid = await store.enqueue("s", "a", {})
    await store.mark_completed(eid, job_id=42)
    event = await store.get_event(eid)
    assert event["status"] == "completed"
    assert event["job_id"] == 42


async def test_mark_completed_sets_ttl(store):
    from event_store import _EVENT_KEY, _TTL_COMPLETED
    eid = await store.enqueue("s", "a", {})
    await store.mark_completed(eid)
    ttl = await store._r.ttl(_EVENT_KEY.format(eid))
    assert 0 < ttl <= _TTL_COMPLETED


# ── mark_deduplicated ─────────────────────────────────────────────────────────

async def test_mark_deduplicated_updates_status(store):
    eid = await store.enqueue("s", "a", {})
    await store.mark_deduplicated(eid)
    event = await store.get_event(eid)
    assert event["status"] == "deduplicated"


# ── schedule_retry ────────────────────────────────────────────────────────────

async def test_schedule_retry_increments_attempts(store):
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()  # remove from pending queue
    await store.schedule_retry(eid, "connection error")
    event = await store.get_event(eid)
    assert event["attempts"] == 1
    assert event["status"] == "pending_retry"
    assert event["last_error"] == "connection error"
    assert event["next_retry_at"] is not None


async def test_schedule_retry_adds_to_retry_queue(store):
    from event_store import _RETRY_Q
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    await store.schedule_retry(eid, "err")
    score = await store._r.zscore(_RETRY_Q, eid)
    assert score is not None and score > time.time()


async def test_schedule_retry_moves_to_dlq_at_max(store):
    from event_store import _DLQ, _MAX_ATTEMPTS
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    for i in range(_MAX_ATTEMPTS):
        await store.schedule_retry(eid, f"err{i}")
    event = await store.get_event(eid)
    assert event["status"] == "dead"
    dlq_ids = [i.decode() if isinstance(i, bytes) else i
               for i in await store._r.lrange(_DLQ, 0, -1)]
    assert eid in dlq_ids


async def test_schedule_retry_multiple_retries(store):
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    await store.schedule_retry(eid, "err1")
    await store.schedule_retry(eid, "err2")
    event = await store.get_event(eid)
    assert event["attempts"] == 2


# ── DLQ management ────────────────────────────────────────────────────────────

async def test_dlq_count_zero(store):
    assert await store.dlq_count() == 0


async def test_dlq_list_empty(store):
    assert await store.dlq_list() == []


async def test_dlq_list_returns_events(store):
    from event_store import _MAX_ATTEMPTS
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    for i in range(_MAX_ATTEMPTS):
        await store.schedule_retry(eid, "err")
    listing = await store.dlq_list()
    assert any(e["id"] == eid for e in listing)
    assert await store.dlq_count() >= 1


async def test_dlq_requeue_success(store):
    from event_store import _MAX_ATTEMPTS
    eid = await store.enqueue("s", "a", {})
    await store.pop_pending()
    for i in range(_MAX_ATTEMPTS):
        await store.schedule_retry(eid, "err")
    result = await store.dlq_requeue(eid)
    assert result is True
    event = await store.get_event(eid)
    assert event["status"] == "pending"
    assert event["attempts"] == 0


async def test_dlq_requeue_nonexistent(store):
    result = await store.dlq_requeue("no-such-id")
    assert result is False


async def test_dlq_clear(store):
    from event_store import _MAX_ATTEMPTS
    for _ in range(2):
        eid = await store.enqueue("s", "a", {})
        await store.pop_pending()
        for i in range(_MAX_ATTEMPTS):
            await store.schedule_retry(eid, "err")
    count = await store.dlq_clear()
    assert count >= 2
    assert await store.dlq_count() == 0


# ── queue_stats ───────────────────────────────────────────────────────────────

async def test_queue_stats_all_zero(store):
    stats = await store.queue_stats()
    assert stats == {"pending": 0, "retry": 0, "dlq": 0}


async def test_queue_stats_after_enqueue(store):
    await store.enqueue("s", "a", {})
    await store.enqueue("s", "b", {})
    stats = await store.queue_stats()
    assert stats["pending"] == 2


# ── is_duplicate ──────────────────────────────────────────────────────────────

async def test_is_duplicate_new_key(store):
    result = await store.is_duplicate("fp-abc", ttl_seconds=60)
    assert result is False


async def test_is_duplicate_second_call(store):
    await store.is_duplicate("fp-xyz", ttl_seconds=60)
    result = await store.is_duplicate("fp-xyz", ttl_seconds=60)
    assert result is True


async def test_is_duplicate_different_fingerprints(store):
    await store.is_duplicate("fp-1", ttl_seconds=60)
    result = await store.is_duplicate("fp-2", ttl_seconds=60)
    assert result is False
