"""
AWX HTTP client with retry logic.

Supports launching any job template by ID so the rule engine can route
different events to different templates.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES        = 3
_BASE_DELAY         = 1.0   # seconds
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class AWXError(Exception):
    """Non-retryable AWX API error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail      = detail
        super().__init__(f"AWX HTTP {status_code}: {detail}")


class AWXClient:
    """Async AWX API client — one instance shared across the app lifespan."""

    def __init__(self, http: httpx.AsyncClient, default_template_id: int) -> None:
        self._http             = http
        self._default_template = default_template_id

    # ── Public API ────────────────────────────────────────────────────────────

    async def launch_job(
        self,
        extra_vars: dict[str, Any] | None = None,
        template_id: int | None = None,
    ) -> dict:
        """
        Launch a job template and return the AWX job object.

        Parameters
        ----------
        extra_vars : dict, optional
            Extra variables merged on top of the template's own extra_vars.
        template_id : int, optional
            Which template to launch.  Falls back to AWX_JOB_TEMPLATE_ID
            from settings when not supplied.
        """
        tid  = template_id if template_id is not None else self._default_template
        url  = f"/api/v2/job_templates/{tid}/launch/"
        body: dict[str, Any] = {"extra_vars": extra_vars or {}}

        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._http.post(url, json=body)

                if resp.status_code in _RETRYABLE_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                if resp.status_code >= 400:
                    raise AWXError(resp.status_code, resp.text)

                job = resp.json()
                logger.info(
                    "Launched template %d → job %s (status=%s)",
                    tid, job.get("id"), job.get("status"),
                )
                return job

            except AWXError:
                raise  # non-retryable — propagate immediately

            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "AWX launch attempt %d/%d failed (%s), retrying in %.1fs",
                        attempt, _MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "AWX launch failed after %d attempts: %s",
                        _MAX_RETRIES, exc,
                    )

        raise RuntimeError(
            f"AWX launch failed after {_MAX_RETRIES} attempts"
        ) from last_exc
