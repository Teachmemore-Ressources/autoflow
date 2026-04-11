"""
Event Scheduler
===============
Fires synthetic events on a cron schedule, feeding them into the rule engine
and ultimately launching AWX jobs — no external webhook required.

Configuration: schedules.yml (mounted as a volume, hot-reloadable)

Example schedules.yml::

    schedules:
      - name: nightly-cleanup
        cron: "0 2 * * *"
        source: scheduler
        action: cleanup
        data:
          environment: production

The dispatch_fn must be an async callable:
    async def dispatch(source: str, action: str, data: dict) -> None
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("scheduler")

DispatchFn = Callable[[str, str, dict], Awaitable[Any]]


class EventScheduler:
    """APScheduler wrapper with YAML-based cron configuration."""

    def __init__(self, schedules_file: str, dispatch_fn: DispatchFn) -> None:
        self._file      = schedules_file
        self._dispatch  = dispatch_fn
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._count     = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> int:
        """Load schedules and start the underlying APScheduler. Returns job count."""
        self._count = self._load()
        self._scheduler.start()
        logger.info("Scheduler started — %d job(s) loaded", self._count)
        return self._count

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def reload(self) -> int:
        """Hot-reload schedules from disk without restarting the service."""
        self._scheduler.remove_all_jobs()
        self._count = self._load()
        logger.info("Scheduler reloaded — %d job(s) active", self._count)
        return self._count

    # ── Introspection ─────────────────────────────────────────────────────────

    def jobs(self) -> list[dict]:
        return [
            {
                "id":       j.id,
                "name":     j.name,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            }
            for j in self._scheduler.get_jobs()
        ]

    @property
    def loaded_count(self) -> int:
        return self._count

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> int:
        try:
            with open(self._file) as fh:
                config = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            logger.warning(
                "Schedules file not found: %s  (scheduler will be idle)", self._file
            )
            return 0
        except yaml.YAMLError as exc:
            logger.error("Invalid YAML in schedules file: %s", exc)
            return 0

        count = 0
        for entry in config.get("schedules", []):
            name   = entry.get("name", f"schedule-{count}")
            cron   = entry.get("cron", "").strip()
            source = entry.get("source", "scheduler")
            action = entry.get("action", "scheduled")
            data   = entry.get("data", {})

            if not cron:
                logger.warning("Schedule '%s' has no cron expression — skipped", name)
                continue

            try:
                trigger = CronTrigger.from_crontab(cron, timezone="UTC")
            except ValueError as exc:
                logger.warning(
                    "Invalid cron '%s' for schedule '%s': %s — skipped", cron, name, exc
                )
                continue

            self._scheduler.add_job(
                self._dispatch,
                trigger,
                id=name,
                name=name,
                args=[source, action, data],
                replace_existing=True,
                misfire_grace_time=60,
            )
            logger.info("  %-35s  %s", name, cron)
            count += 1

        return count
