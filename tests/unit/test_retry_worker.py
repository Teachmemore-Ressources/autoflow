"""Unit tests for services/event-engine/retry_worker.py (RetryWorker)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_store():
    store = MagicMock()
    store.pop_pending = AsyncMock(return_value=[])
    store.pop_due_retries = AsyncMock(return_value=[])
    store.get_event = AsyncMock(return_value=None)
    store.mark_completed = AsyncMock()
    store.mark_deduplicated = AsyncMock()
    store.schedule_retry = AsyncMock()
    return store


def _make_dispatch(result=None):
    result = result or {"status": "accepted", "job_id": 7}
    return AsyncMock(return_value=result)


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_start_creates_two_tasks():
    from retry_worker import RetryWorker

    store = _make_store()
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_start_and_stop(worker))
    finally:
        loop.close()


async def _start_and_stop(worker):
    worker.start()
    assert len(worker._tasks) == 2
    worker.stop()
    for t in worker._tasks:
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=0.1)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


def test_stop_cancels_all_tasks():
    from retry_worker import RetryWorker

    store = _make_store()
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    cancelled = []

    async def run():
        worker.start()
        for t in worker._tasks:
            real_cancel = t.cancel

            def _track_cancel(msg=None, _orig=real_cancel):
                cancelled.append(True)
                return _orig(msg)

            t.cancel = _track_cancel
        worker.stop()

    asyncio.run(run())
    assert len(cancelled) == 2


# ── _process: success paths ───────────────────────────────────────────────────


async def test_process_calls_mark_completed():
    from retry_worker import RetryWorker

    store = _make_store()
    store.get_event = AsyncMock(
        return_value={
            "source": "ci",
            "action": "deploy",
            "data": {},
        }
    )
    dispatch = _make_dispatch({"status": "accepted", "job_id": 99})
    worker = RetryWorker(store, dispatch)

    await worker._process("evt-1")

    store.mark_completed.assert_awaited_once_with("evt-1", job_id=99)
    store.mark_deduplicated.assert_not_awaited()
    store.schedule_retry.assert_not_awaited()


async def test_process_calls_mark_deduplicated():
    from retry_worker import RetryWorker

    store = _make_store()
    store.get_event = AsyncMock(
        return_value={
            "source": "s",
            "action": "a",
            "data": {},
        }
    )
    dispatch = _make_dispatch({"status": "deduplicated"})
    worker = RetryWorker(store, dispatch)

    await worker._process("evt-2")

    store.mark_deduplicated.assert_awaited_once_with("evt-2")
    store.mark_completed.assert_not_awaited()


# ── _process: error / retry paths ────────────────────────────────────────────


async def test_process_schedules_retry_on_dispatch_error():
    from retry_worker import RetryWorker

    store = _make_store()
    store.get_event = AsyncMock(
        return_value={
            "source": "s",
            "action": "a",
            "data": {},
        }
    )
    dispatch = AsyncMock(side_effect=RuntimeError("AWX unreachable"))
    worker = RetryWorker(store, dispatch)

    await worker._process("evt-3")

    store.schedule_retry.assert_awaited_once()
    call_args = store.schedule_retry.call_args
    assert call_args[0][0] == "evt-3"
    assert "AWX unreachable" in call_args[0][1]


async def test_process_skips_missing_event():
    from retry_worker import RetryWorker

    store = _make_store()
    store.get_event = AsyncMock(return_value=None)
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    await worker._process("ghost-id")

    dispatch.assert_not_awaited()
    store.mark_completed.assert_not_awaited()
    store.schedule_retry.assert_not_awaited()


# ── _run_pending: CancelledError exits cleanly ────────────────────────────────


async def test_run_pending_exits_on_cancel():
    from retry_worker import RetryWorker

    store = _make_store()
    store.pop_pending = AsyncMock(side_effect=asyncio.CancelledError)
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    task = asyncio.create_task(worker._run_pending())
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass  # expected — task propagates CancelledError
    assert task.done()


async def test_run_retry_exits_on_cancel():
    from retry_worker import RetryWorker

    store = _make_store()
    store.pop_due_retries = AsyncMock(side_effect=asyncio.CancelledError)
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    task = asyncio.create_task(worker._run_retry())
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    assert task.done()


async def test_run_pending_handles_non_cancel_exception():
    from retry_worker import RetryWorker

    call_count = 0

    async def boom():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("redis down")
        raise asyncio.CancelledError

    store = _make_store()
    store.pop_pending = boom
    dispatch = _make_dispatch()
    worker = RetryWorker(store, dispatch)

    with patch("retry_worker.POLL_INTERVAL", 0):
        task = asyncio.create_task(worker._run_pending())
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    assert call_count >= 1
