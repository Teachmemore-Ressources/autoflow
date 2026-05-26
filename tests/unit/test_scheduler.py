"""Unit tests for services/event-engine/scheduler.py (EventScheduler)."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _noop_dispatch(source, action, data):
    pass


def _make_scheduler(tmp_path, yaml_content: str = ""):
    from scheduler import EventScheduler
    schedules_file = tmp_path / "schedules.yml"
    if yaml_content:
        schedules_file.write_text(yaml_content)
    return EventScheduler(str(schedules_file), _noop_dispatch)


# ── _load: file not found ─────────────────────────────────────────────────────

def test_load_missing_file_returns_zero(tmp_path):
    from scheduler import EventScheduler
    sched = EventScheduler(str(tmp_path / "no_file.yml"), _noop_dispatch)
    with patch.object(sched._scheduler, "start"):
        count = sched.start()
    assert count == 0


def test_load_missing_file_does_not_raise(tmp_path):
    from scheduler import EventScheduler
    sched = EventScheduler(str(tmp_path / "missing.yml"), _noop_dispatch)
    with patch.object(sched._scheduler, "start"):
        sched.start()


# ── _load: invalid YAML ───────────────────────────────────────────────────────

def test_load_invalid_yaml_returns_zero(tmp_path):
    f = tmp_path / "bad.yml"
    f.write_text("}{not valid yaml")
    from scheduler import EventScheduler
    sched = EventScheduler(str(f), _noop_dispatch)
    with patch.object(sched._scheduler, "start"):
        count = sched.start()
    assert count == 0


# ── _load: valid YAML with schedules ─────────────────────────────────────────

def test_load_valid_schedule(tmp_path):
    yaml = textwrap.dedent("""\
        schedules:
          - name: nightly-check
            cron: "0 2 * * *"
            source: scheduler
            action: cleanup
            data:
              env: prod
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job") as mock_add:
            count = sched.start()
    assert count == 1
    mock_add.assert_called_once()


def test_load_multiple_schedules(tmp_path):
    yaml = textwrap.dedent("""\
        schedules:
          - name: job1
            cron: "0 1 * * *"
            source: scheduler
            action: a
          - name: job2
            cron: "0 3 * * *"
            source: scheduler
            action: b
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job") as mock_add:
            count = sched.start()
    assert count == 2
    assert mock_add.call_count == 2


def test_load_empty_yaml_returns_zero(tmp_path):
    f = tmp_path / "empty.yml"
    f.write_text("")
    sched = _make_scheduler(tmp_path)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job"):
            count = sched.start()
    assert count == 0


# ── _load: edge cases ─────────────────────────────────────────────────────────

def test_load_skips_entry_without_cron(tmp_path, caplog):
    yaml = textwrap.dedent("""\
        schedules:
          - name: no-cron-job
            source: scheduler
            action: something
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job") as mock_add:
            count = sched.start()
    assert count == 0
    mock_add.assert_not_called()


def test_load_skips_invalid_cron(tmp_path, caplog):
    yaml = textwrap.dedent("""\
        schedules:
          - name: bad-cron-job
            cron: "not a cron"
            source: scheduler
            action: something
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job") as mock_add:
            count = sched.start()
    assert count == 0
    mock_add.assert_not_called()


def test_load_uses_defaults_for_optional_fields(tmp_path):
    yaml = textwrap.dedent("""\
        schedules:
          - cron: "0 4 * * *"
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job") as mock_add:
            count = sched.start()
    assert count == 1
    call_kwargs = mock_add.call_args[1]
    assert call_kwargs["args"][0] == "scheduler"
    assert call_kwargs["args"][1] == "scheduled"


# ── start / stop / reload ─────────────────────────────────────────────────────

def test_start_returns_count(tmp_path):
    yaml = textwrap.dedent("""\
        schedules:
          - name: j1
            cron: "*/5 * * * *"
            source: s
            action: a
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "start"):
        with patch.object(sched._scheduler, "add_job"):
            count = sched.start()
    assert count == 1
    assert sched.loaded_count == 1


def test_stop_calls_shutdown_when_running(tmp_path):
    from unittest.mock import PropertyMock
    sched = _make_scheduler(tmp_path)
    with patch.object(type(sched._scheduler), "running", new_callable=PropertyMock, return_value=True):
        with patch.object(sched._scheduler, "shutdown") as mock_shutdown:
            sched.stop()
    mock_shutdown.assert_called_once_with(wait=False)


def test_stop_does_not_call_shutdown_when_not_running(tmp_path):
    from unittest.mock import PropertyMock
    sched = _make_scheduler(tmp_path)
    with patch.object(type(sched._scheduler), "running", new_callable=PropertyMock, return_value=False):
        with patch.object(sched._scheduler, "shutdown") as mock_shutdown:
            sched.stop()
    mock_shutdown.assert_not_called()


def test_reload_removes_all_jobs_then_reloads(tmp_path):
    yaml = textwrap.dedent("""\
        schedules:
          - name: reload-job
            cron: "0 6 * * *"
            source: s
            action: a
    """)
    sched = _make_scheduler(tmp_path, yaml)
    with patch.object(sched._scheduler, "remove_all_jobs") as mock_remove:
        with patch.object(sched._scheduler, "add_job"):
            count = sched.reload()
    mock_remove.assert_called_once()
    assert count == 1


# ── jobs() introspection ──────────────────────────────────────────────────────

def test_jobs_returns_list(tmp_path):
    from datetime import datetime, timezone
    sched = _make_scheduler(tmp_path)
    mock_job = MagicMock()
    mock_job.id = "j1"
    mock_job.name = "nightly"
    mock_job.next_run_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    sched._scheduler.get_jobs = MagicMock(return_value=[mock_job])
    result = sched.jobs()
    assert len(result) == 1
    assert result[0]["id"] == "j1"
    assert result[0]["name"] == "nightly"
    assert "2030" in result[0]["next_run"]


def test_jobs_handles_none_next_run(tmp_path):
    sched = _make_scheduler(tmp_path)
    mock_job = MagicMock()
    mock_job.id = "j2"
    mock_job.name = "paused"
    mock_job.next_run_time = None
    sched._scheduler.get_jobs = MagicMock(return_value=[mock_job])
    result = sched.jobs()
    assert result[0]["next_run"] is None


def test_jobs_empty(tmp_path):
    sched = _make_scheduler(tmp_path)
    sched._scheduler.get_jobs = MagicMock(return_value=[])
    assert sched.jobs() == []
